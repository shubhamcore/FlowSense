# FlowSense AI

> **Turn every store camera into a retail intelligence sensor.**

FlowSense turns existing CCTV footage into anonymous customer trajectories, store-space analytics, bottleneck explanations and evidence-led layout experiments. The included dashboard is a deterministic, offline-friendly hackathon demo with a real algorithm module and a zero-dependency API server.

## 60-second demo

1. Run `python server.py`.
2. Open the live preview or `http://localhost:8000`.
3. Start on **Overview**. Point out the six KPI cards and provenance tags.
4. Switch the digital twin from **Traffic** to **Dwell**, then **Congestion**.
5. Open **Bottlenecks** and explain Aisle 4 using the evidence chain: density `+184%`, speed `-43%`, dwell `+67%`, 327 affected visitors.
6. Open **Simulator**, toggle **Move promo display**, and click **Run simulation**.
7. Show **Experiments**: observed comparisons stay separate from simulated results and insufficient sample sizes are called out.
8. Ask the **AI analyst**: “Where are customers getting stuck?” The answer cites current analytics.

Demo narration:

> “Retail stores already have cameras. The problem is that those cameras watch customers, but do not understand them. FlowSense converts passive CCTV into anonymous journeys, finds friction, explains the evidence, and lets a retailer test a layout change before moving a fixture. We measure behavior, not identity.”

## Run locally

No npm install or external CDN is required.

```bash
python server.py
# dashboard + API: http://localhost:8000
```

The server binds to `0.0.0.0` for preview environments. The UI also renders as a standalone `public/index.html`: if the API is unavailable, the deterministic demo data keeps the experience functional.

## What is real vs demo

Every visible metric is labeled:

- **MEASURED** — calculated from an observed processing result.
- **ESTIMATED** — an analytical score or statistical approximation.
- **SIMULATED** — returned by the what-if layout model; never an observed result.
- **SYNTHETIC** — deterministic hackathon dataset used to guarantee a compelling demo.

The included store snapshot is synthetic by design. It reliably contains a high-dwell promo island, a high-traffic / low-dwell zone, a persistent Aisle 4 bottleneck and two dead-zone candidates. It must not be presented as a claim about a real store.

## Architecture

```text
                         MP4 / RTSP / CCTV
                                  |
                                  v
                       +----------------------+
                       | Video ingestion      |
                       | validation + sampling|
                       +----------+-----------+
                                  |
                    +-------------v--------------+
                    | Detector: person only     |
                    | YOLO / TensorRT / edge    |
                    +-------------+--------------+
                                  |
                    +-------------v--------------+
                    | Anonymous tracker         |
                    | ByteTrack / BoT-SORT      |
                    +-------------+--------------+
                                  |
                    +-------------v--------------+
                    | Centroid + smoothing      |
                    | no face / no identity     |
                    +-------------+--------------+
                                  |
                    +-------------v--------------+
                    | Homography: camera ->     |
                    | floor coordinates         |
                    +-------------+--------------+
                                  |
            +---------------------+---------------------+
            |                     |                     |
            v                     v                     v
        zone engine          dwell engine         movement engine
            |                     |                     |
            +---------------------+---------------------+
                                  v
                       +----------------------+
                       | journey reconstruction|
                       | path clusters + stops |
                       +----------+-----------+
                                  |
             +--------------------+---------------------+
             |                    |                     |
             v                    v                     v
         heatmaps            bottlenecks          dead zones
             |                    |                     |
             +--------------------+---------------------+
                                  v
                       +----------------------+
                       | root cause + evidence |
                       +----------+-----------+
                                  v
                  +---------------+----------------+
                  | recommendation / simulator  |
                  | experiments / AI analyst    |
                  +-------------------------------+
```

### Production split

The current `server.py` is intentionally dependency-free for a hackathon preview. A pilot deployment should split it into:

- **Ingestion API**: validates MP4 / RTSP sources, sanitizes filenames, creates a processing job.
- **CV worker**: samples frames, runs person-only YOLO, associates tracks with BoT-SORT or ByteTrack, masks faces at the edge and emits centroids.
- **Analytics worker**: maps centroids through a calibrated homography, assigns zones and writes immutable events.
- **Analytics API**: serves cached snapshots to the dashboard and the grounded analyst.
- **Postgres / Timescale**: zones, tracks, events, journeys, snapshots and experiments; indexes on `store_id`, `camera_id`, `timestamp` and `zone_id`.
- **Redis / queue**: processing status and short-lived live state.
- **Object storage**: encrypted raw footage with a short configurable retention policy, or edge-only processing to avoid upload entirely.

## Core analytics

### Dwell

For each anonymous track and contiguous zone visit:

```text
zone_entry_time = first point inside zone
zone_exit_time  = last point inside zone
dwell_seconds   = zone_exit_time - zone_entry_time
```

Visits below 2 seconds are `TRANSIT`; visits at or above 5 seconds are `DWELL`; the middle range remains `PAUSED`. Thresholds are configurable in Settings. The `flowsense/core.py` implementation prevents accidental boundary crossings from inflating dwell.

### Bottleneck score

```text
score = 100 * (
    .28 * normalized_density +
    .18 * normalized_dwell +
    .24 * normalized_speed_reduction +
    .16 * persistence +
    .14 * occupancy
)
```

Weights are exposed as a future store-specific configuration. A bottleneck explanation always shows the observation, evidence, hypothesis and confidence.

### Dead-zone score

```text
score = 100 * (
    .38 * traffic_gap +
    .28 * dwell_gap +
    .18 * visibility_gap +
    .16 * connectivity_gap
)
```

Zero traffic is not required. The engine compares a zone with neighboring paths, visibility and connectivity before calling it an opportunity.

### Homography

`flowsense/core.py` contains a deterministic 4-point homography solver and point projection. Production calibration should allow four or more reference points, reject degenerate configurations, store the matrix per camera and show a calibration confidence / reprojection error in Settings.

## API surface

The dependency-free API server currently exposes:

```text
GET  /healthz
GET  /api/analytics/overview
GET  /api/analytics/bottlenecks
GET  /api/analytics/dead-zones
GET  /api/analytics/sections
GET  /api/cameras
POST /api/videos/process          {"filename":"camera01.mp4"}
POST /api/simulation/run          {"baseline":{...},"display_moved":true}
POST /api/ai/query                {"question":"Where are customers stuck?"}
GET  /api/actions
POST /api/actions
POST /api/actions/{id}/complete
POST /api/actions/{id}/measure
GET  /api/flow-genome
GET  /api/proof/summary
POST /api/experiments/analyze
```

A production API should add authenticated store scoping and endpoints for:

```text
POST /videos/upload
GET  /processing/status/{job_id}
GET  /analytics/heatmap?mode=dwell
GET  /analytics/action-frontier
GET  /analytics/dwell?zone_id=...
GET  /analytics/journeys
POST /experiments
GET  /experiments/{id}
POST /api/v1/stores/{store_id}/actions
POST /api/v1/actions/{action_id}/complete
POST /api/v1/actions/{action_id}/measure
GET  /api/v1/stores/{store_id}/flow-genome
GET  /camera/health
```

## Data model

Recommended relationships:

- `stores` 1—N `cameras`
- `stores` 1—N `store_layouts`
- `store_layouts` 1—N `zones`
- `cameras` 1—N `tracks`
- `tracks` 1—N `track_points`
- `journeys` 1—N `journey_zones`
- `analytics_snapshots` 1—N `bottlenecks`, `dead_zones`, `recommendations`
- `experiments` 1—N `simulations`

Only anonymous tracking IDs should enter the analytics database. Never add face embeddings, names, phone numbers, demographic inference or identity matching.

## Tests

The pure analytics tests cover point-in-polygon zone assignment, dwell classification, journey segmentation, bottleneck and dead-zone scoring, homography projection and simulation labeling:

```bash
python -m unittest discover -s tests -v
```

## Product innovations added for the demo

- **Decision loop view** — Observe → Understand → Diagnose → Recommend → Simulate → Measure makes the value chain immediately legible to judges.
- **Guided judge mode** — a 90-second presenter flow with stage-specific cues keeps the demo focused on the decision, not the code.
- **Replayable live map** — the operator can scrub the demo snapshot and switch between traffic, dwell, congestion and opportunity layers.
- **Counterfactual mechanism card** — the simulator explains what mechanism is being tested, which metrics should move, and why it is not a revenue forecast.
- **Grounded AI analyst** — responses cite the exact snapshot signals used instead of returning generic retail advice.
- **Local CV fallback** — `flowsense/cv_pipeline.py` can process an MP4 with OpenCV’s built-in HOG person detector and anonymous nearest-centroid tracking when a heavyweight model is unavailable. This keeps the demo offline while leaving a clean adapter boundary for YOLO + ByteTrack / BoT-SORT.
- **Trust surface** — metric provenance, confidence, sample-size warnings and privacy constraints are part of the primary interface.
- **Action Frontier / Store Flow Fingerprint** — interventions are ranked by impact, confidence, feasibility and effort; the system also exposes path entropy and an active-learning priority so it can say when it needs more evidence before recommending a change.
- **Operational action loop** — an insight becomes an owned action with a due date and success metric, then transitions through complete → measure → learn instead of disappearing into a dashboard.
- **Flow Genome moat** — aggregate store fingerprints compound validated intervention outcomes and surface similar-store hypotheses without cross-store person tracking or identity data.

## Production upgrade path now included

The repository now contains an installable production deployment path in addition to the zero-dependency demo:

```text
YOLO11 / RT-DETR
      ↓
ByteTrack or BoT-SORT
      ↓
Redis + RQ GPU worker queue
      ↓
PostgreSQL + TimescaleDB
      ↓
FastAPI control plane + JWT tenant isolation
      ↓
MinIO / S3 private object storage
      ↓
Prometheus metrics + structured logs + load test
```

### Files

- `requirements-production.txt` — FastAPI, SQLAlchemy, Redis/RQ, Ultralytics, S3, JWT, Prometheus and load-test dependencies.
- `production/vision.py` — YOLO11 / RT-DETR adapter with person-only inference, ByteTrack / BoT-SORT selection and anonymous job-scoped IDs.
- `production/worker.py` — Redis/RQ queue entrypoints for GPU workers and job status updates.
- `production/schema.sql` — PostgreSQL + TimescaleDB schema for tenants, stores, cameras, calibrations, jobs, track points, handoffs, journeys, snapshots, recommendations and experiments.
- `production/api.py` — authenticated FastAPI control plane with multi-store tenancy, calibration endpoint, job creation/status, retention policy and Prometheus metrics.
- `production/calibration.py` — homography plus reprojection-error validation.
- `production/handoff.py` — privacy-preserving camera handoff using a short spatial-temporal gate, not face recognition.
- `production/storage.py` — tenant-scoped object keys and retention deletion helpers.
- `production/observability.py` — Prometheus counters, histograms, queue depth and structured events.
- `production/load_test.py` — repeatable concurrent smoke/load test with p50/p95/RPS output.
- `infra/docker-compose.yml` — local Postgres/TimescaleDB, Redis, MinIO, API, GPU worker and Prometheus stack.
- `Dockerfile.production` — container image for the production API/worker.

### Start the production-shaped stack

```bash
# This path requires Docker and the production dependencies.
docker compose -f infra/docker-compose.yml up --build

# Development token endpoint is disabled when ENVIRONMENT=production.
curl -X POST http://localhost:8080/api/v1/auth/dev-token \\
  -H 'content-type: application/json' \\
  -d '{"tenant_id":"demo-tenant","role":"admin","allowed_stores":["store-flagship"]}'

# Basic load smoke test against the API.
python -m production.load_test --url http://localhost:8080/healthz --requests 200 --concurrency 20
```

### Production safety requirements

Before exposing this stack to real stores:

1. Set a high-entropy `JWT_SECRET`; disable `/api/v1/auth/dev-token` with `ENVIRONMENT=production`.
2. Add an OIDC provider and replace the dev token path with signed identity claims.
3. Put RLS policies on tenant tables and set `app.tenant_id` on every database transaction.
4. Use private encrypted S3/MinIO buckets, KMS-managed keys, lifecycle rules and a deletion audit log.
5. Deploy one queue per GPU, pin model/tracker versions and record model hashes in each `processing_job`.
6. Configure four or more calibration points per camera and alert on reprojection error above the store threshold.
7. Keep camera handoff short-lived and spatial-temporal. Do not add face embeddings or cross-store identity.
8. Set SLOs for ingest latency, job success rate, tracker confidence, camera FPS and queue depth; alert through Prometheus/Grafana.
9. Run the load test in CI and add an end-to-end fixture with a known trajectory ground truth.

## Limitations and next steps

- The demo dataset is synthetic; upload flow demonstrates the processing experience but does not ship a heavyweight CV model.
- A production tracker must handle camera hand-off, occlusion, re-identification within a short anonymous window and dropped frames.
- Revenue impact is intentionally not estimated without a sales connector. The product reports exposure, efficiency and utilization instead.
- Predictive mode should be enabled only after enough historical snapshots and should show confidence intervals.
- Before/after experiments need matched time windows, sample-size checks and, ideally, randomized or quasi-experimental design.

## Privacy promise

**FlowSense measures behavior, not identity.** Prefer edge processing, short raw-video retention, face masking, encrypted transport and configurable deletion. The interface makes provenance visible because trust is part of the product, not a footer.
