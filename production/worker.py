"""Redis-backed GPU worker entrypoints.

Run workers separately from the API so expensive inference never blocks a web
request. In production deploy one queue per GPU and set CUDA_VISIBLE_DEVICES.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

try:
    from redis import Redis
    from rq import Queue
except ImportError:  # optional until production dependencies are installed
    Redis = Queue = None

from flowsense.core import apply_homography
from .observability import QUEUE_DEPTH, configure_logging, log_event, observe_job
from .vision import UltralyticsPersonTracker, VisionConfig, VisionDependencyError


def redis_client():
    if Redis is None:
        raise RuntimeError("Install redis and rq to run the production worker")
    return Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def job_key(job_id: str) -> str:
    return f"flowsense:job:{job_id}"


def set_job_status(client, job_id: str, **values):
    values["updated_at"] = datetime.now(timezone.utc).isoformat()
    client.hset(job_key(job_id), mapping={k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in values.items()})


def enqueue_video(*, job_id: str, tenant_id: str, store_id: str, camera_id: str, source_uri: str, vision_config: dict[str, Any] | None = None) -> str:
    client = redis_client()
    queue_name = os.environ.get("GPU_QUEUE", "flowsense-gpu-0")
    queue = Queue(queue_name, connection=client, default_timeout=3600)
    set_job_status(client, job_id, status="QUEUED", progress=0, tenant_id=tenant_id, store_id=store_id, camera_id=camera_id)
    job = queue.enqueue(process_video_job, job_id=job_id, tenant_id=tenant_id, store_id=store_id, camera_id=camera_id, source_uri=source_uri, vision_config=vision_config or {})
    QUEUE_DEPTH.labels(queue=queue_name).set(len(queue))
    return job.id


def process_video_job(*, job_id: str, tenant_id: str, store_id: str, camera_id: str, source_uri: str, vision_config: dict[str, Any]) -> dict:
    client = redis_client()
    config = VisionConfig(**{k: v for k, v in vision_config.items() if k in VisionConfig.__dataclass_fields__})
    set_job_status(client, job_id, status="RUNNING", progress=5, model=config.model_name, tracker=config.tracker_name, device=config.device)
    observations = 0
    tracks: set[str] = set()
    with observe_job(tenant=tenant_id, model=config.model_name, tracker=config.tracker_name):
        try:
            tracker = UltralyticsPersonTracker(config)
            for observation in tracker.track_video(source_uri, job_id=job_id, camera_id=camera_id):
                observations += 1
                tracks.add(observation.anonymous_key)
                # Persist a compact JSON record or enqueue a DB batch write. Do
                # not persist frames, crops or the local track ID.
                client.rpush(f"{job_key(job_id)}:observations", json.dumps(observation.as_dict()))
                if observations % 100 == 0:
                    set_job_status(client, job_id, progress=min(95, 5 + observations // 10), tracks=len(tracks), observations=observations)
        except VisionDependencyError:
            set_job_status(client, job_id, status="FAILED", progress=100, error_code="VISION_DEPENDENCY_MISSING")
            raise
    result = {"status": "COMPLETE", "tracks": len(tracks), "observations": observations, "privacy": "anonymous_centroids_only"}
    set_job_status(client, job_id, status="COMPLETE", progress=100, result=result)
    log_event("analytics_ready", tenant=tenant_id, store=store_id, camera=camera_id, tracks=len(tracks), observations=observations)
    return result


if __name__ == "__main__":
    configure_logging()
    if Queue is None:
        raise SystemExit("Install requirements-production.txt before starting a worker")
    import rq
    with redis_client() as client:
        worker = rq.Worker([os.environ.get("GPU_QUEUE", "flowsense-gpu-0")], connection=client)
        worker.work()
