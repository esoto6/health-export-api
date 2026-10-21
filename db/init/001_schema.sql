
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(100) NOT NULL,
    email varchar(255) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS api_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name varchar(100) NOT NULL,
    key_hash varchar(64) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys(user_id);

CREATE TABLE IF NOT EXISTS ingestion_batches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    received_at timestamptz NOT NULL DEFAULT now(),
    payload_sha256 varchar(64) NOT NULL,
    source varchar(100) NOT NULL DEFAULT 'auto_health_export',
    item_count integer NOT NULL DEFAULT 0,
    inserted_count integer NOT NULL DEFAULT 0,
    skipped_count integer NOT NULL DEFAULT 0,
    payload jsonb NOT NULL,
    CONSTRAINT uq_ingestion_batch_user_payload
        UNIQUE (user_id, payload_sha256)
);
CREATE INDEX IF NOT EXISTS ix_ingestion_batches_user_received
    ON ingestion_batches(user_id, received_at DESC);

CREATE TABLE IF NOT EXISTS health_metrics (
    time timestamptz NOT NULL,
    sample_key varchar(64) NOT NULL,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    metric varchar(100) NOT NULL,
    value double precision NOT NULL,
    unit varchar(40) NOT NULL,
    end_time timestamptz,
    source varchar(200) NOT NULL DEFAULT 'unknown',
    device varchar(200),
    resolution varchar(20) NOT NULL DEFAULT 'raw',
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (time, sample_key),
    CONSTRAINT ck_health_metrics_resolution
        CHECK (resolution IN ('raw', 'daily', 'weekly', 'monthly'))
);

SELECT create_hypertable(
    'health_metrics', by_range('time'), if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_health_metrics_user_metric_time
    ON health_metrics(user_id, metric, time DESC);
CREATE INDEX IF NOT EXISTS ix_health_metrics_user_metric_resolution_time
    ON health_metrics(user_id, metric, resolution, time DESC);

CREATE TABLE IF NOT EXISTS workouts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_id varchar(200) NOT NULL,
    source varchar(100) NOT NULL DEFAULT 'Apple Health',
    workout_type varchar(100) NOT NULL,
    start_time timestamptz NOT NULL,
    end_time timestamptz NOT NULL,
    duration_seconds double precision,
    total_energy_kcal double precision,
    active_energy_kcal double precision,
    distance_miles double precision,
    average_heart_rate_bpm double precision,
    max_heart_rate_bpm double precision,
    min_heart_rate_bpm double precision,
    effort_score double precision,
    source_name varchar(200),
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_workout_user_source_source_id
        UNIQUE (user_id, source, source_id),
    CONSTRAINT ck_workouts_time CHECK (end_time >= start_time)
);

CREATE INDEX IF NOT EXISTS ix_workouts_user_start_time
    ON workouts(user_id, start_time DESC);
CREATE INDEX IF NOT EXISTS ix_workouts_user_type_start
    ON workouts(user_id, workout_type, start_time DESC);

CREATE TABLE IF NOT EXISTS workout_samples (
    id bigserial PRIMARY KEY,
    workout_id uuid NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    time timestamptz NOT NULL,
    phase varchar(20) NOT NULL DEFAULT 'workout',
    metric varchar(100) NOT NULL,
    value double precision NOT NULL,
    unit varchar(40) NOT NULL,
    source varchar(200),
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_workout_sample_phase CHECK (phase IN ('workout', 'recovery'))
);
CREATE INDEX IF NOT EXISTS ix_workout_samples_workout_time
    ON workout_samples(workout_id, time);
CREATE INDEX IF NOT EXISTS ix_workout_samples_workout_metric_time
    ON workout_samples(workout_id, metric, time);

CREATE TABLE IF NOT EXISTS workout_routes (
    id bigserial PRIMARY KEY,
    workout_id uuid NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    recorded_at timestamptz NOT NULL,
    sequence integer NOT NULL,
    latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    altitude_meters double precision,
    speed double precision,
    course_degrees double precision,
    horizontal_accuracy_meters double precision,
    vertical_accuracy_meters double precision,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_workout_route_sequence UNIQUE (workout_id, sequence)
);
CREATE INDEX IF NOT EXISTS ix_workout_routes_workout_time
    ON workout_routes(workout_id, recorded_at);

CREATE TABLE IF NOT EXISTS workout_intervals (
    id bigserial PRIMARY KEY,
    workout_id uuid NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    interval_type varchar(20) NOT NULL CHECK (interval_type IN ('segment', 'split')),
    sequence integer NOT NULL,
    start_time timestamptz,
    end_time timestamptz,
    duration_seconds double precision,
    target_distance double precision,
    target_distance_unit varchar(20),
    metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_workout_interval_sequence
        UNIQUE (workout_id, interval_type, sequence)
);
CREATE INDEX IF NOT EXISTS ix_workout_intervals_workout_type
    ON workout_intervals(workout_id, interval_type);

CREATE OR REPLACE VIEW daily_metric_summary AS
SELECT
    user_id,
    time_bucket('1 day', time, 'America/New_York') AS day,
    metric,
    unit,
    sum(value) AS sum_value,
    avg(value) AS avg_value,
    min(value) AS min_value,
    max(value) AS max_value,
    count(*) AS samples,
    resolution
FROM health_metrics
GROUP BY
    user_id,
    time_bucket('1 day', time, 'America/New_York'),
    metric,
    unit,
    resolution;

CREATE OR REPLACE VIEW daily_workout_summary AS
SELECT
    user_id,
    date_trunc('day', start_time AT TIME ZONE 'UTC') AS day_utc,
    workout_type,
    count(*) AS workout_count,
    sum(coalesce(duration_seconds, extract(epoch FROM end_time - start_time)))
        AS duration_seconds,
    sum(total_energy_kcal) AS total_energy_kcal,
    sum(active_energy_kcal) AS active_energy_kcal,
    sum(distance_miles) AS distance_miles,
    avg(average_heart_rate_bpm) AS avg_heart_rate_bpm,
    max(max_heart_rate_bpm) AS max_heart_rate_bpm,
    min(min_heart_rate_bpm) AS min_heart_rate_bpm,
    avg(effort_score) AS avg_effort_score
FROM workouts
GROUP BY
    user_id,
    date_trunc('day', start_time AT TIME ZONE 'UTC'),
    workout_type;

GRANT USAGE ON SCHEMA public TO grafana_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO grafana_reader;
