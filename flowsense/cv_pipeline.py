"""Offline-first computer-vision worker for FlowSense.

This module gives the dashboard a credible processing path without requiring a
large model download. When OpenCV is available it uses its built-in HOG person
detector and a deterministic nearest-centroid association. In a pilot, swap
``HogPersonDetector`` for a YOLO + ByteTrack adapter behind the same interface.
No face recognition or identity attributes are generated.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Sequence

try:
    import cv2
except ImportError:  # pragma: no cover - the API can still expose a useful error
    cv2 = None

from .core import Zone, assign_zone


@dataclass
class PipelineStage:
    key: str
    label: str
    status: str = "PENDING"
    detail: str = ""


@dataclass
class AnonymousObservation:
    track_id: str
    t: float
    x: float
    y: float
    confidence: float
    zone_id: str | None = None


class CVUnavailable(RuntimeError):
    pass


class HogPersonDetector:
    """Small, offline fallback detector. Replace with YOLO in production."""

    def __init__(self):
        if cv2 is None:
            raise CVUnavailable("OpenCV is not installed")
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame):
        boxes, weights = self.hog.detectMultiScale(
            frame, winStride=(8, 8), padding=(8, 8), scale=1.05
        )
        results = []
        for (x, y, w, h), weight in zip(boxes, weights):
            confidence = float(weight[0] if hasattr(weight, "__len__") else weight)
            # HOG weights are not calibrated probabilities. Keep them bounded
            # and label the backend in the job metadata instead of pretending.
            results.append((int(x), int(y), int(w), int(h), max(0.0, min(1.0, confidence / 2.0))))
        return results


def _associate(detections, active, next_id, max_distance=95.0):
    assignments = []
    used = set()
    for x, y, w, h, confidence in detections:
        cx, cy = x + w / 2, y + h / 2
        candidate = None
        best = max_distance
        for track_id, previous in active.items():
            if track_id in used:
                continue
            dx, dy = previous[0] - cx, previous[1] - cy
            distance = (dx * dx + dy * dy) ** .5
            if distance < best:
                candidate, best = track_id, distance
        if candidate is None:
            candidate = f"visitor_{next_id:04d}"
            next_id += 1
        active[candidate] = (cx, cy)
        used.add(candidate)
        assignments.append((candidate, cx, cy, confidence))
    return assignments, next_id


def process_video(
    path: str | Path,
    *,
    zones: Iterable[Zone] = (),
    sample_fps: float = 2.0,
    max_frames: int = 3600,
) -> dict:
    """Process a local video into anonymous observations and stage metadata.

    The result is intentionally JSON-serializable so a queue worker can persist
    it or publish status events. The HOG adapter is a fallback; a production
    deployment should use a GPU-backed person detector and ByteTrack/BoT-SORT.
    """
    stages = [PipelineStage("ingest", "Video ingestion"), PipelineStage("detect", "Person detection"), PipelineStage("track", "Anonymous tracking"), PipelineStage("trajectory", "Trajectory reconstruction"), PipelineStage("zones", "Zone + dwell analysis"), PipelineStage("insights", "Insights + recommendations")]
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".mp4":
        raise ValueError("Only MP4 input is supported by this demo worker")
    if cv2 is None:
        raise CVUnavailable("Install opencv-python to enable local CV processing")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("Video could not be opened; check the codec")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps if frame_count else 0.0
    stride = max(1, round(fps / max(0.25, sample_fps)))
    detector = HogPersonDetector()
    active = {}
    next_id = 1
    observations: list[AnonymousObservation] = []
    zones = tuple(zones)
    frame_index = 0
    processed = 0

    stages[0].status = "DONE"
    stages[0].detail = f"{fps:.1f} FPS · {duration:.1f}s · sampled every {stride} frames"
    while processed < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % stride:
            frame_index += 1
            continue
        processed += 1
        detections = detector.detect(frame)
        if detections:
            stages[1].status = "DONE"
            assignments, next_id = _associate(detections, active, next_id)
            stages[2].status = "DONE"
            for track_id, x, y, confidence in assignments:
                zone = assign_zone((x, y), zones) if zones else None
                observations.append(AnonymousObservation(track_id, frame_index / fps, x, y, confidence, zone))
        frame_index += 1
    cap.release()
    stages[1].detail = f"{len(observations)} person observations"
    stages[2].detail = f"{len({o.track_id for o in observations})} anonymous tracks"
    if observations:
        stages[3].status = stages[4].status = stages[5].status = "DONE"
        stages[3].detail = "Centroids emitted; no identity features stored"
        stages[4].detail = f"{len({o.zone_id for o in observations if o.zone_id})} configured zones touched"
        stages[5].detail = "Ready for cached analytics snapshot"
    else:
        stages[1].status = "NO_DATA"
        stages[1].detail = "No person detections; retain a useful error state"
        for stage in stages[2:]:
            stage.status = "BLOCKED"
            stage.detail = "No person observations available"
    return {
        "status": "complete" if observations else "no_people_detected",
        "backend": "opencv_hog_fallback",
        "privacy": "anonymous_centroids_only",
        "video": {"fps": fps, "frames": frame_count, "duration_sec": round(duration, 2)},
        "observations": [asdict(o) for o in observations],
        "stages": [asdict(s) for s in stages],
    }
