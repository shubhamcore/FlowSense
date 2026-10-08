"""Camera calibration service helpers.

The UI submits four or more camera-space and floor-space reference points.
The service calculates a homography and rejects poor reprojection error rather
than silently accepting an uncalibrated camera.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Sequence

from flowsense.core import Point, apply_homography, solve_homography


@dataclass(frozen=True)
class CalibrationResult:
    homography: list[list[float]]
    reprojection_error_m: float
    quality: str
    points_used: int


def calibrate(source_points: Sequence[Point], floor_points: Sequence[Point], *, max_error_m: float = 0.35) -> CalibrationResult:
    if len(source_points) != len(floor_points) or len(source_points) < 4:
        raise ValueError("Calibration needs at least four paired points")
    # The core solver uses four points. Extra points are used for quality
    # validation after solving from the first four stable anchors.
    h = solve_homography(source_points[:4], floor_points[:4])
    errors = []
    for source, target in zip(source_points, floor_points):
        projected = apply_homography(source, h)
        errors.append(hypot(projected[0] - target[0], projected[1] - target[1]))
    error = sum(errors) / len(errors)
    quality = "GOOD" if error <= max_error_m else ("REVIEW" if error <= max_error_m * 2 else "REJECT")
    if quality == "REJECT":
        raise ValueError(f"Calibration reprojection error {error:.3f}m exceeds the safe limit")
    return CalibrationResult(h, round(error, 4), quality, len(source_points))
