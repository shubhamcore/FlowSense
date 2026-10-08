"""Metrics and structured observability primitives."""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager

try:
    from prometheus_client import Counter, Gauge, Histogram
except ImportError:  # keeps unit tests and the offline demo dependency-free
    class _Metric:
        def labels(self, **kwargs): return self
        def inc(self, *args, **kwargs): return None
        def set(self, *args, **kwargs): return None
        def observe(self, *args, **kwargs): return None
    Counter = Gauge = Histogram = lambda *a, **k: _Metric()

JOB_STARTED = Counter("flowsense_jobs_started_total", "CV jobs started", ["tenant", "model", "tracker"])
JOB_COMPLETED = Counter("flowsense_jobs_completed_total", "CV jobs completed", ["tenant", "status"])
JOB_LATENCY = Histogram("flowsense_job_seconds", "CV job duration", ["model"], buckets=(1, 5, 15, 30, 60, 120, 300, 900))
QUEUE_DEPTH = Gauge("flowsense_queue_depth", "Pending GPU jobs", ["queue"])
DETECTION_CONFIDENCE = Histogram("flowsense_detection_confidence", "Person detection confidence", ["model"], buckets=(.1, .3, .5, .7, .8, .9, .95, 1.0))

logger = logging.getLogger("flowsense")


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def log_event(event: str, **fields):
    logger.info(json.dumps({"event": event, "ts": time.time(), **fields}, separators=(",", ":")))


@contextmanager
def observe_job(*, tenant: str, model: str, tracker: str):
    started = time.perf_counter()
    JOB_STARTED.labels(tenant=tenant, model=model, tracker=tracker).inc()
    try:
        yield
    except Exception:
        JOB_COMPLETED.labels(tenant=tenant, status="ERROR").inc()
        log_event("job_error", tenant=tenant, model=model, tracker=tracker)
        raise
    else:
        JOB_COMPLETED.labels(tenant=tenant, status="SUCCESS").inc()
        log_event("job_complete", tenant=tenant, model=model, tracker=tracker)
    finally:
        JOB_LATENCY.labels(model=model).observe(time.perf_counter() - started)
