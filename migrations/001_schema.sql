CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS ingestion_batches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  received_at timestamptz NOT NULL DEFAULT now(),
  payload_sha256 char(64) NOT NULL UNIQUE,
  source varchar(100) NOT NULL DEFAULT 'auto_health_export',
  item_count integer NOT NULL DEFAULT 0,
  inserted_count integer NOT NULL DEFAULT 0,
  skipped_count integer NOT NULL DEFAULT 0,
  payload jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS health_metrics (
  time timestamptz NOT NULL,
  sample_key char(64) NOT NULL,
  metric varchar(100) NOT NULL,
  value double precision NOT NULL,
  unit varchar(40) NOT NULL,
  end_time timestamptz,
  source varchar(200) NOT NULL DEFAULT 'unknown',
  device varchar(200),
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (time, sample_key)
);
SELECT create_hypertable('health_metrics', by_range('time'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS ix_health_metrics_metric_time ON health_metrics(metric, time DESC);

CREATE TABLE IF NOT EXISTS workouts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id varchar(200) NOT NULL,
  source varchar(100) NOT NULL DEFAULT 'Apple Health',
  workout_type varchar(100) NOT NULL,
  start_time timestamptz NOT NULL,
  end_time timestamptz NOT NULL,
  duration_seconds double precision,
  total_energy_kcal double precision,
  distance_meters double precision,
  source_name varchar(200),
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT uq_workout_source_source_id UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS ix_workouts_start_time ON workouts(start_time DESC);
CREATE INDEX IF NOT EXISTS ix_workouts_type_start ON workouts(workout_type, start_time DESC);

CREATE OR REPLACE VIEW daily_metric_summary AS
SELECT time_bucket('1 day', time) AS day, metric, unit, sum(value) AS sum_value,
       avg(value) AS avg_value, min(value) AS min_value, max(value) AS max_value, count(*) AS samples
FROM health_metrics GROUP BY 1,2,3;

CREATE OR REPLACE VIEW daily_workout_summary AS
SELECT date_trunc('day', start_time AT TIME ZONE 'UTC') AS day_utc, workout_type,
       count(*) AS workout_count,
       sum(coalesce(duration_seconds, extract(epoch FROM end_time-start_time))) AS duration_seconds,
       sum(total_energy_kcal) AS energy_kcal, sum(distance_meters) AS distance_meters
FROM workouts GROUP BY 1,2;

GRANT USAGE ON SCHEMA public TO grafana_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_reader;
