"""GPU-capable detector/tracker adapter.

Ultralytics exposes YOLO11 and RT-DETR models and supports ByteTrack and
BoT-SORT configuration files. The adapter emits only person centroids and
anonymous, job-scoped identifiers; it never persists crops or face features.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Iterator

try:
    from ultralytics import RTDETR, YOLO
except ImportError:  # optional in the offline hackathon environment
    RTDETR = YOLO = None


@dataclass(frozen=True)
class VisionConfig:
    model_name: str = "yolo11n.pt"
    tracker_name: str = "bytetrack.yaml"
    device: str = "0"  # CUDA device; set cpu explicitly for a CPU worker
    confidence: float = .35
    iou: float = .5
    image_size: int = 960
    persist_frames: int = 30


@dataclass(frozen=True)
class PersonObservation:
    anonymous_key: str
    camera_id: str
    frame_index: int
    timestamp_s: float
    x_px: float
    y_px: float
    confidence: float
    local_track_id: str

    def as_dict(self):
        return asdict(self)


class VisionDependencyError(RuntimeError):
    pass


class UltralyticsPersonTracker:
    def __init__(self, config: VisionConfig = VisionConfig()):
        if YOLO is None or RTDETR is None:
            raise VisionDependencyError("Install requirements-production.txt for YOLO11 / RT-DETR")
        self.config = config
        model_name = config.model_name.lower()
        self.model = RTDETR(config.model_name) if "rtdetr" in model_name else YOLO(config.model_name)

    def _anon_key(self, job_id: str, camera_id: str, local_id: str) -> str:
        return "visitor_" + sha256(f"{job_id}:{camera_id}:{local_id}".encode()).hexdigest()[:16]

    def track_video(self, source: str | Path, *, job_id: str, camera_id: str, fps: float = 24.0) -> Iterator[PersonObservation]:
        """Stream person-only observations from a video or RTSP source."""
        stream = self.model.track(
            source=str(source), stream=True, persist=True,
            tracker=self.config.tracker_name,
            classes=[0], conf=self.config.confidence, iou=self.config.iou,
            imgsz=self.config.image_size, device=self.config.device,
            verbose=False,
        )
        for frame_index, result in enumerate(stream):
            boxes = getattr(result, "boxes", None)
            if boxes is None or boxes.id is None:
                continue
            xyxy = boxes.xyxy.cpu().tolist()
            ids = boxes.id.int().cpu().tolist()
            confs = boxes.conf.cpu().tolist()
            for box, local_id, confidence in zip(xyxy, ids, confs):
                x1, y1, x2, y2 = box
                yield PersonObservation(
                    anonymous_key=self._anon_key(job_id, camera_id, str(local_id)),
                    camera_id=camera_id,
                    frame_index=frame_index,
                    timestamp_s=round(frame_index / max(fps, .1), 3),
                    x_px=round((x1 + x2) / 2, 2),
                    y_px=round(y2, 2),  # bottom-center anchor for floor mapping
                    confidence=round(float(confidence), 4),
                    local_track_id=str(local_id),
                )
