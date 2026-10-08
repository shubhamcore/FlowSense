"""Benchmark metrics for the FlowSense proof package.

Input frame records use:
  {"frame": 0, "objects": [{"id": "gt-1", "bbox": [x1,y1,x2,y2]}]}

The implementation is dependency-light so it can run in CI. For a formal
benchmark, replace greedy matching with the challenge's official evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, median
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Match:
    frame: int
    gt_id: str
    pred_id: str
    iou: float


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1, ix2, iy2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return inter / (area_a + area_b - inter) if area_a + area_b - inter else 0.0


def _greedy_matches(gt_frames: Sequence[Mapping], pred_frames: Sequence[Mapping], threshold: float = .5) -> list[Match]:
    gt_by_frame = {int(row["frame"]): row.get("objects", []) for row in gt_frames}
    pred_by_frame = {int(row["frame"]): row.get("objects", []) for row in pred_frames}
    matches: list[Match] = []
    for frame in sorted(set(gt_by_frame) | set(pred_by_frame)):
        candidates = []
        for gt in gt_by_frame.get(frame, []):
            for pred in pred_by_frame.get(frame, []):
                score = iou(gt["bbox"], pred["bbox"])
                if score >= threshold:
                    candidates.append((score, str(gt["id"]), str(pred["id"])))
        used_gt, used_pred = set(), set()
        for score, gt_id, pred_id in sorted(candidates, reverse=True):
            if gt_id in used_gt or pred_id in used_pred:
                continue
            used_gt.add(gt_id); used_pred.add(pred_id)
            matches.append(Match(frame, gt_id, pred_id, score))
    return matches


def tracking_metrics(gt_frames: Sequence[Mapping], pred_frames: Sequence[Mapping], iou_threshold: float = .5) -> dict:
    matches = _greedy_matches(gt_frames, pred_frames, iou_threshold)
    gt_count = sum(len(row.get("objects", [])) for row in gt_frames)
    pred_count = sum(len(row.get("objects", [])) for row in pred_frames)
    tp = len(matches)
    fp = max(0, pred_count - tp)
    fn = max(0, gt_count - tp)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    # IDF1 from matched frame associations and a one-to-one global ID map.
    pair_counts: dict[tuple[str, str], int] = {}
    for match in matches:
        pair_counts[(match.gt_id, match.pred_id)] = pair_counts.get((match.gt_id, match.pred_id), 0) + 1
    used_gt, used_pred, idtp = set(), set(), 0
    for (gt_id, pred_id), count in sorted(pair_counts.items(), key=lambda item: item[1], reverse=True):
        if gt_id not in used_gt and pred_id not in used_pred:
            used_gt.add(gt_id); used_pred.add(pred_id); idtp += count
    idfp = max(0, pred_count - idtp)
    idfn = max(0, gt_count - idtp)
    idf1 = (2 * idtp) / (2 * idtp + idfp + idfn) if 2 * idtp + idfp + idfn else 0.0
    deta = tp / (tp + .5 * fp + .5 * fn) if tp + .5 * fp + .5 * fn else 0.0
    assa = idtp / tp if tp else 0.0
    hota = sqrt(max(0.0, deta * assa))
    return {"precision": round(precision, 4), "recall": round(recall, 4), "idf1": round(idf1, 4), "hota": round(hota, 4), "mean_iou": round(mean([match.iou for match in matches]), 4) if matches else 0.0, "tp": tp, "fp": fp, "fn": fn, "frames": len(set(int(row["frame"]) for row in gt_frames) | set(int(row["frame"]) for row in pred_frames))}


def dwell_mae(ground_truth: Mapping[str, float], predictions: Mapping[str, float]) -> dict:
    keys = sorted(set(ground_truth) & set(predictions))
    errors = [abs(float(predictions[key]) - float(ground_truth[key])) for key in keys]
    return {"mae_seconds": round(mean(errors), 4) if errors else None, "median_absolute_error_seconds": round(median(errors), 4) if errors else None, "matched_visits": len(keys), "missing_ground_truth": len(set(ground_truth) - set(predictions)), "extra_predictions": len(set(predictions) - set(ground_truth))}


def calibration_error(projected: Sequence[Sequence[float]], target: Sequence[Sequence[float]]) -> dict:
    if len(projected) != len(target) or not projected:
        raise ValueError("Projected and target calibration points must have equal non-zero length")
    errors = [sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) for a, b in zip(projected, target)]
    return {"mean_error_m": round(mean(errors), 4), "max_error_m": round(max(errors), 4), "points": len(errors)}


def latency_summary(samples_ms: Iterable[float]) -> dict:
    values = sorted(float(x) for x in samples_ms)
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}
    p95 = values[min(len(values) - 1, int(len(values) * .95))]
    return {"count": len(values), "p50_ms": round(values[len(values) // 2], 2), "p95_ms": round(p95, 2), "mean_ms": round(mean(values), 2)}


def proof_report(*, gt_frames, pred_frames, gt_dwell, pred_dwell, projected_points, target_points, latency_ms, provenance: str) -> dict:
    return {"provenance": provenance, "metrics": {"tracking": tracking_metrics(gt_frames, pred_frames), "dwell": dwell_mae(gt_dwell, pred_dwell), "calibration": calibration_error(projected_points, target_points), "latency": latency_summary(latency_ms)}, "limitations": ["Greedy IoU matching is a CI smoke evaluator, not an official benchmark evaluator.", "Replace SYNTHETIC_FIXTURE with an annotated, consented retail clip before presenting model quality as observed.", "Metrics are not revenue claims."]}
