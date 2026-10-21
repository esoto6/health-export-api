BEGIN;

ALTER TABLE health_metrics
    ADD COLUMN IF NOT EXISTS resolution varchar(20) NOT NULL DEFAULT 'raw';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_health_metrics_resolution'
          AND conrelid = 'health_metrics'::regclass
    ) THEN
        ALTER TABLE health_metrics
            ADD CONSTRAINT ck_health_metrics_resolution
            CHECK (resolution IN ('raw', 'daily', 'weekly', 'monthly'));
    END IF;
END
$$;

-- Historical Auto Health Export rollups do not include an explicit resolution.
-- Infer it only for additive metrics whose neighboring records demonstrate a
-- weekly cadence. High-frequency samples remain raw; future daily/monthly
-- exports are labeled by the ingestion classifier or explicit source metadata.
WITH ordered AS (
    SELECT
        time,
        sample_key,
        metric,
        extract(epoch FROM time - lag(time) OVER series) AS previous_gap,
        extract(epoch FROM lead(time) OVER series - time) AS next_gap
    FROM health_metrics
    WHERE metric IN (
        'active_energy',
        'apple_exercise_time',
        'apple_stand_time',
        'basal_energy_burned',
        'cycling_distance',
        'dietary_water',
        'flights_climbed',
        'step_count',
        'time_in_daylight',
        'walking_running_distance'
    )
    WINDOW series AS (PARTITION BY user_id, metric, unit ORDER BY time)
), classified AS (
    SELECT
        time,
        sample_key,
        CASE
            WHEN previous_gap BETWEEN 596160 AND 613440
              OR next_gap BETWEEN 596160 AND 613440 THEN 'weekly'
            ELSE 'raw'
        END AS inferred_resolution
    FROM ordered
)
UPDATE health_metrics AS metric
SET resolution = classified.inferred_resolution
FROM classified
WHERE metric.time = classified.time
  AND metric.sample_key = classified.sample_key
  AND metric.resolution = 'raw'
  AND classified.inferred_resolution <> 'raw';

-- Partial final buckets lose their seven-day neighbor once detailed live
-- samples begin. In a series already proven to contain weekly rollups, its
-- remaining local-midnight bucket is part of that same export cadence.
UPDATE health_metrics AS metric
SET resolution = 'weekly'
WHERE metric.metric IN (
        'active_energy',
        'apple_exercise_time',
        'apple_stand_time',
        'basal_energy_burned',
        'cycling_distance',
        'dietary_water',
        'flights_climbed',
        'step_count',
        'time_in_daylight',
        'walking_running_distance'
    )
  AND (metric.time AT TIME ZONE 'America/New_York')::time = time '00:00:00'
  AND EXISTS (
      SELECT 1
      FROM health_metrics AS sibling
      WHERE sibling.user_id = metric.user_id
        AND sibling.metric = metric.metric
        AND sibling.unit = metric.unit
        AND sibling.resolution = 'weekly'
  );

CREATE INDEX IF NOT EXISTS ix_health_metrics_user_metric_resolution_time
    ON health_metrics(user_id, metric, resolution, time DESC);

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

GRANT SELECT ON daily_metric_summary TO grafana_reader;

COMMIT;
