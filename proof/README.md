# FlowSense Proof Package

This is the reproducible quality-evidence layer for the hackathon demo.

```bash
python -m proof.run --output proof_report.json
```

The checked-in fixture is intentionally labeled `SYNTHETIC_FIXTURE`. It is for CI and pipeline demonstration only; it must not be presented to judges as measured retail performance.

## Metrics

- Detection precision and recall
- IDF1
- HOTA-style association score
- Mean IoU
- Dwell-time MAE and median absolute error
- Calibration mean and maximum reprojection error
- p50 / p95 / mean processing latency

## Replacing the fixture with real evidence

1. Obtain a consented, privacy-reviewed retail clip or a public dataset.
2. Annotate person boxes and stable anonymous IDs for a representative window.
3. Export the same `frames`, `dwell_seconds`, `projected_points`, `target_points` and `latency_ms` fields.
4. Run:

```bash
python -m proof.run \
  --ground-truth data/annotated/ground_truth.json \
  --predictions data/annotated/predictions.json \
  --provenance REAL_ANNOTATED_CLIP \
  --output reports/store-01-proof.json
```

5. Record model name, model hash, tracker config, GPU, resolution, FPS and dataset ID beside the report.
6. Only show results as observed after a human has checked the annotations and the report has been reviewed.
