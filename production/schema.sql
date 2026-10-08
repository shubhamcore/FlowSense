-- FlowSense production schema for PostgreSQL + TimescaleDB.
-- Run with the database role that owns the analytics schema.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner','admin','analyst','operator','viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);
CREATE TABLE IF NOT EXISTS stores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    area_m2 NUMERIC(10,2),
    active_layout_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stores_tenant_idx ON stores(tenant_id);
CREATE TABLE IF NOT EXISTS store_layouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','ACTIVE','ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS zones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    layout_id UUID NOT NULL REFERENCES store_layouts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'section',
    polygon JSONB NOT NULL,
    transit_threshold_seconds NUMERIC(8,2) NOT NULL DEFAULT 2,
    dwell_threshold_seconds NUMERIC(8,2) NOT NULL DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cameras (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    retention_hours INTEGER NOT NULL DEFAULT 24,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store_id, external_id)
);
CREATE INDEX IF NOT EXISTS cameras_store_idx ON cameras(store_id);
CREATE TABLE IF NOT EXISTS camera_calibrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    source_points JSONB NOT NULL,
    floor_points JSONB NOT NULL,
    homography JSONB NOT NULL,
    reprojection_error_m NUMERIC(10,4) NOT NULL,
    calibrated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS calibration_camera_idx ON camera_calibrations(camera_id, active);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    camera_id UUID REFERENCES cameras(id),
    source_object_key TEXT NOT NULL,
    model_name TEXT NOT NULL,
    tracker_name TEXT NOT NULL,
    device TEXT NOT NULL DEFAULT 'cpu',
    status TEXT NOT NULL DEFAULT 'QUEUED',
    progress SMALLINT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    error_code TEXT,
    error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS jobs_tenant_status_idx ON processing_jobs(tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS anonymous_tracks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES processing_jobs(id) ON DELETE CASCADE,
    camera_id UUID NOT NULL REFERENCES cameras(id),
    anonymous_key TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    point_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (job_id, anonymous_key)
);
CREATE INDEX IF NOT EXISTS tracks_camera_time_idx ON anonymous_tracks(camera_id, started_at DESC);
CREATE TABLE IF NOT EXISTS track_points (
    time TIMESTAMPTZ NOT NULL,
    track_id UUID NOT NULL REFERENCES anonymous_tracks(id) ON DELETE CASCADE,
    camera_id UUID NOT NULL REFERENCES cameras(id),
    x_m NUMERIC(10,3) NOT NULL,
    y_m NUMERIC(10,3) NOT NULL,
    speed_mps NUMERIC(10,3),
    confidence NUMERIC(5,4),
    zone_id UUID REFERENCES zones(id),
    state TEXT CHECK (state IN ('MOVING','PAUSED','DWELLING','CONGESTED'))
);
SELECT create_hypertable('track_points', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS track_points_zone_time_idx ON track_points(zone_id, time DESC);

CREATE TABLE IF NOT EXISTS camera_handoffs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    from_camera_id UUID NOT NULL REFERENCES cameras(id),
    to_camera_id UUID NOT NULL REFERENCES cameras(id),
    from_track_id UUID NOT NULL REFERENCES anonymous_tracks(id),
    to_track_id UUID NOT NULL REFERENCES anonymous_tracks(id),
    handoff_time TIMESTAMPTZ NOT NULL,
    confidence NUMERIC(5,4) NOT NULL,
    method TEXT NOT NULL DEFAULT 'SPATIAL_TEMPORAL_GATE'
);
CREATE INDEX IF NOT EXISTS handoff_store_time_idx ON camera_handoffs(store_id, handoff_time DESC);

CREATE TABLE IF NOT EXISTS journeys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    anonymous_journey_key TEXT NOT NULL,
    entry_at TIMESTAMPTZ NOT NULL,
    exit_at TIMESTAMPTZ,
    duration_seconds NUMERIC(10,2),
    distance_m NUMERIC(10,2),
    stop_count INTEGER NOT NULL DEFAULT 0,
    revisit_count INTEGER NOT NULL DEFAULT 0,
    checkout_reached BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS journey_zones (
    journey_id UUID NOT NULL REFERENCES journeys(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL,
    zone_id UUID NOT NULL REFERENCES zones(id),
    entry_at TIMESTAMPTZ NOT NULL,
    exit_at TIMESTAMPTZ,
    dwell_seconds NUMERIC(10,2),
    classification TEXT NOT NULL,
    PRIMARY KEY (journey_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    data_class TEXT NOT NULL CHECK (data_class IN ('MEASURED','ESTIMATED','SIMULATED','SYNTHETIC')),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS snapshots_store_window_idx ON analytics_snapshots(store_id, window_end DESC);
CREATE TABLE IF NOT EXISTS bottlenecks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES analytics_snapshots(id) ON DELETE CASCADE,
    zone_id UUID REFERENCES zones(id),
    score NUMERIC(6,2) NOT NULL,
    severity TEXT NOT NULL,
    evidence JSONB NOT NULL,
    hypothesis TEXT,
    confidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dead_zones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES analytics_snapshots(id) ON DELETE CASCADE,
    zone_id UUID REFERENCES zones(id),
    score NUMERIC(6,2) NOT NULL,
    evidence JSONB NOT NULL,
    opportunity_score NUMERIC(6,2),
    confidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES analytics_snapshots(id) ON DELETE CASCADE,
    problem TEXT NOT NULL,
    evidence JSONB NOT NULL,
    intervention TEXT NOT NULL,
    expected_effect TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE TABLE IF NOT EXISTS experiments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    baseline_layout_id UUID NOT NULL REFERENCES store_layouts(id),
    variant_layout_id UUID REFERENCES store_layouts(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    sample_size_required INTEGER,
    confidence TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS simulations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID REFERENCES experiments(id) ON DELETE CASCADE,
    input_payload JSONB NOT NULL,
    output_payload JSONB NOT NULL,
    data_class TEXT NOT NULL DEFAULT 'SIMULATED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS operational_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    source_recommendation_id UUID REFERENCES recommendations(id),
    title TEXT NOT NULL,
    problem TEXT NOT NULL,
    intervention TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    success_metric TEXT NOT NULL,
    owner TEXT NOT NULL,
    due_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS actions_store_status_idx ON operational_actions(store_id, status, due_date);
CREATE TABLE IF NOT EXISTS action_measurements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id UUID NOT NULL REFERENCES operational_actions(id) ON DELETE CASCADE,
    baseline_window JSONB NOT NULL,
    after_window JSONB NOT NULL,
    analysis JSONB NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS flow_genomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    fingerprint_hash TEXT NOT NULL,
    journey_entropy NUMERIC(8,5) NOT NULL,
    payload JSONB NOT NULL,
    data_class TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store_id, fingerprint_hash)
);
CREATE INDEX IF NOT EXISTS genomes_store_time_idx ON flow_genomes(store_id, generated_at DESC);
CREATE TABLE IF NOT EXISTS genome_peer_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_genome_id UUID NOT NULL REFERENCES flow_genomes(id) ON DELETE CASCADE,
    intervention TEXT NOT NULL,
    observed_effect JSONB NOT NULL,
    decision TEXT NOT NULL,
    guardrail TEXT,
    validated_at TIMESTAMPTZ
);

-- The application sets these per transaction after authenticating the JWT.
-- ALTER TABLE ... ENABLE ROW LEVEL SECURITY should be enabled in a managed deployment.
-- Example policy pattern:
--   current_setting('app.tenant_id', true)::uuid = tenant_id
