"""Run a reproducible FlowSense proof report.

Default uses the checked-in synthetic fixture and loudly labels provenance.
For a real evaluation pass the same JSON shape exported from an annotated
clip and set ``--provenance REAL_ANNOTATED_CLIP`` only when that is true.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .metrics import proof_report

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", default=str(ROOT / "fixtures" / "ground_truth.json"))
    parser.add_argument("--predictions", default=str(ROOT / "fixtures" / "predictions.json"))
    parser.add_argument("--provenance", default="SYNTHETIC_FIXTURE")
    parser.add_argument("--output", default="proof_report.json")
    args = parser.parse_args()
    gt, pred = load(args.ground_truth), load(args.predictions)
    report = proof_report(gt_frames=gt["frames"], pred_frames=pred["frames"], gt_dwell=gt.get("dwell_seconds", {}), pred_dwell=pred.get("dwell_seconds", {}), projected_points=gt["projected_points"], target_points=gt["target_points"], latency_ms=gt.get("latency_ms", []), provenance=args.provenance)
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
