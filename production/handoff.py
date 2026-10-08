"""Privacy-preserving multi-camera handoff.

This is not face recognition or long-term re-identification. A handoff is only
allowed inside a configured overlap/portal, within a short time window, and
when motion direction is compatible. The emitted key is scoped to one store
and one observation window.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import hypot
from typing import Sequence


@dataclass(frozen=True)
class TrackExit:
    camera_id: str
    local_track_id: str
    timestamp_s: float
    x_m: float
    y_m: float
    vx_mps: float
    vy_mps: float


@dataclass(frozen=True)
class TrackEntry:
    camera_id: str
    local_track_id: str
    timestamp_s: float
    x_m: float
    y_m: float
    vx_mps: float
    vy_mps: float


@dataclass(frozen=True)
class Handoff:
    journey_key: str
    from_track: str
    to_track: str
    confidence: float
    method: str = "SPATIAL_TEMPORAL_GATE"


def _unit_similarity(a: tuple[float, float], b: tuple[float, float]) -> float:
    an = hypot(*a) or 1.0
    bn = hypot(*b) or 1.0
    return max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (an * bn)))


def match_handoff(
    exit: TrackExit,
    entry: TrackEntry,
    *,
    portal_center: tuple[float, float],
    max_gap_s: float = 6.0,
    max_portal_distance_m: float = 3.0,
    min_direction_similarity: float = 0.25,
    store_secret: str = "dev-only-change-me",
) -> Handoff | None:
    if exit.camera_id == entry.camera_id:
        return None
    gap = entry.timestamp_s - exit.timestamp_s
    if gap < 0 or gap > max_gap_s:
        return None
    exit_distance = hypot(exit.x_m - portal_center[0], exit.y_m - portal_center[1])
    entry_distance = hypot(entry.x_m - portal_center[0], entry.y_m - portal_center[1])
    if max(exit_distance, entry_distance) > max_portal_distance_m:
        return None
    direction = _unit_similarity((exit.vx_mps, exit.vy_mps), (entry.vx_mps, entry.vy_mps))
    if direction < min_direction_similarity:
        return None
    spatial = max(0.0, 1.0 - max(exit_distance, entry_distance) / max_portal_distance_m)
    temporal = max(0.0, 1.0 - gap / max_gap_s)
    confidence = round(max(0.0, min(1.0, .45 * spatial + .35 * temporal + .20 * ((direction + 1) / 2))), 4)
    # The key is intentionally short-lived and store-scoped. It cannot be used
    # to identify a person outside this store/session.
    material = f"{store_secret}:{exit.camera_id}:{exit.local_track_id}:{entry.camera_id}:{entry.local_track_id}:{int(entry.timestamp_s // 60)}"
    journey_key = "j_" + sha256(material.encode()).hexdigest()[:16]
    return Handoff(journey_key, exit.local_track_id, entry.local_track_id, confidence)
