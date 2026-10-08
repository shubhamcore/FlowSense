"""FlowSense analytics primitives.

The demo UI consumes deterministic snapshots, but these functions are the same
pure building blocks a production worker can call after a detector/tracker
has emitted anonymous centroids. There is intentionally no identity or face
recognition code in this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import hypot
from statistics import median
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

Point = Tuple[float, float]


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    polygon: Tuple[Point, ...]
    category: str = "section"


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Return True when point is inside a polygon using the ray-cast rule."""
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        crosses = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi)
        if crosses:
            inside = not inside
        j = i
    return inside


def assign_zone(point: Point, zones: Iterable[Zone]) -> str | None:
    for zone in zones:
        if point_in_polygon(point, zone.polygon):
            return zone.id
    return None


def _close_run(run: dict, output: list, transit_threshold: float, dwell_threshold: float) -> None:
    if not run.get("zone_id") or not run.get("points"):
        return
    start = run["points"][0]["t"]
    end = run["points"][-1]["t"]
    duration = max(0.0, end - start)
    classification = "TRANSIT" if duration < transit_threshold else ("DWELL" if duration >= dwell_threshold else "PAUSED")
    output.append({
        "zone_id": run["zone_id"],
        "entry": start,
        "exit": end,
        "duration": round(duration, 3),
        "classification": classification,
    })


def dwell_events(track_points: Sequence[Mapping], zones: Iterable[Zone], *, transit_threshold: float = 2.0, dwell_threshold: float = 5.0) -> list[dict]:
    """Convert one anonymous track into contiguous zone visits.

    Input points must have ``t`` seconds and ``x``/``y`` floor coordinates.
    Short boundary crossings remain visible as TRANSIT events rather than
    inflating dwell time.
    """
    zone_list = list(zones)
    events: list[dict] = []
    run = {"zone_id": None, "points": []}
    for point in sorted(track_points, key=lambda p: p["t"]):
        zone_id = assign_zone((point["x"], point["y"]), zone_list)
        if zone_id != run["zone_id"]:
            _close_run(run, events, transit_threshold, dwell_threshold)
            run = {"zone_id": zone_id, "points": []}
        if zone_id:
            run["points"].append(point)
    _close_run(run, events, transit_threshold, dwell_threshold)
    return events


def journey_summary(track_id: str, points: Sequence[Mapping], zones: Iterable[Zone]) -> dict:
    events = dwell_events(points, zones)
    ordered = [e["zone_id"] for e in events]
    distance = sum(hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in zip(points, points[1:]))
    return {
        "track_id": track_id,
        "entry": points[0]["t"] if points else None,
        "exit": points[-1]["t"] if points else None,
        "duration": round((points[-1]["t"] - points[0]["t"]) if len(points) > 1 else 0, 2),
        "zones": ordered,
        "dwell_by_zone": {e["zone_id"]: e["duration"] for e in events},
        "distance": round(distance, 2),
        "stops": sum(e["classification"] == "DWELL" for e in events),
        "revisits": len(ordered) - len(set(ordered)),
        "events": events,
    }


def _norm(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def bottleneck_score(metrics: Mapping[str, float], weights: Mapping[str, float] | None = None) -> float:
    """Composite score: density, dwell, speed reduction, persistence, occupancy."""
    weights = weights or {"density": .28, "dwell": .18, "speed_reduction": .24, "persistence": .16, "occupancy": .14}
    values = {key: max(0.0, min(1.0, float(metrics.get(key, 0)))) for key in weights}
    total = sum(weights.get(key, 0) * values.get(key, 0) for key in weights)
    return round(total * 100, 1)


def dead_zone_score(metrics: Mapping[str, float]) -> float:
    """Dead-zone score from traffic gap, dwell gap and accessibility evidence."""
    traffic_gap = max(0.0, min(1.0, float(metrics.get("traffic_gap", 0))))
    dwell_gap = max(0.0, min(1.0, float(metrics.get("dwell_gap", 0))))
    visibility_gap = max(0.0, min(1.0, float(metrics.get("visibility_gap", 0))))
    connectivity_gap = max(0.0, min(1.0, float(metrics.get("connectivity_gap", 0))))
    return round((traffic_gap * .38 + dwell_gap * .28 + visibility_gap * .18 + connectivity_gap * .16) * 100, 1)


def solve_homography(source: Sequence[Point], target: Sequence[Point]) -> list[list[float]]:
    """Solve an 8-parameter planar homography for four point pairs."""
    if len(source) != 4 or len(target) != 4:
        raise ValueError("Exactly four source and target points are required")
    matrix, vector = [], []
    for (x, y), (u, v) in zip(source, target):
        matrix += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        vector += [u, v]
    # Gaussian elimination with partial pivoting.
    for col in range(8):
        pivot = max(range(col, 8), key=lambda row: abs(matrix[row][col]))
        if abs(matrix[pivot][col]) < 1e-10:
            raise ValueError("Calibration points are degenerate")
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        vector[col], vector[pivot] = vector[pivot], vector[col]
        divisor = matrix[col][col]
        matrix[col] = [x / divisor for x in matrix[col]]
        vector[col] /= divisor
        for row in range(8):
            if row == col:
                continue
            factor = matrix[row][col]
            matrix[row] = [a - factor * b for a, b in zip(matrix[row], matrix[col])]
            vector[row] -= factor * vector[col]
    return [vector[:3], vector[3:6], [vector[6], vector[7], 1.0]]


def apply_homography(point: Point, h: Sequence[Sequence[float]]) -> Point:
    x, y = point
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    if abs(w) < 1e-12:
        raise ValueError("Invalid homography projection")
    return ((h[0][0] * x + h[0][1] * y + h[0][2]) / w, (h[1][0] * x + h[1][1] * y + h[1][2]) / w)


def summarize_sections(journeys: Sequence[Mapping], zone_names: Mapping[str, str]) -> list[dict]:
    by_zone = defaultdict(list)
    visitors = defaultdict(set)
    for journey in journeys:
        for event in journey.get("events", []):
            by_zone[event["zone_id"]].append(float(event.get("duration", 0)))
            visitors[event["zone_id"]].add(journey["track_id"])
    result = []
    for zone_id, durations in by_zone.items():
        result.append({
            "zone_id": zone_id,
            "name": zone_names.get(zone_id, zone_id),
            "visitors": len(visitors[zone_id]),
            "avg_dwell": round(sum(durations) / len(durations), 1),
            "median_dwell": round(median(durations), 1),
            "p90_dwell": round(sorted(durations)[max(0, int(len(durations) * .9) - 1)], 1),
            "total_dwell": round(sum(durations), 1),
            "revisits": 0,
        })
    return sorted(result, key=lambda item: item["visitors"], reverse=True)


def simulate_layout(baseline: Mapping[str, float], *, display_moved: bool = False, aisle_widened: bool = False) -> dict:
    """Transparent deterministic what-if model; output is explicitly simulated."""
    congestion = float(baseline.get("congestion", 42.0))
    dead_zone_exposure = float(baseline.get("dead_zone_exposure", 68.0))
    journey_efficiency = float(baseline.get("journey_efficiency", 71.0))
    if display_moved:
        congestion *= .762
        dead_zone_exposure *= .84
        journey_efficiency = min(100, journey_efficiency + 8)
    if aisle_widened:
        congestion *= .81
        dead_zone_exposure *= .92
        journey_efficiency = min(100, journey_efficiency + 11)
    return {
        "congestion": round(congestion, 1),
        "dead_zone_exposure": round(dead_zone_exposure, 1),
        "journey_efficiency": round(journey_efficiency, 1),
        "label": "SIMULATED — NOT OBSERVED RESULT",
    }
