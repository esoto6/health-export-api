-- Heart-rate time series
SELECT time AS "time", value AS "Heart rate", unit
FROM health_metrics
WHERE $__timeFilter(time)
  AND metric IN ('heart_rate', 'HKQuantityTypeIdentifierHeartRate')
ORDER BY time;

-- Workouts table; use dashboard time range to filter workout starts
SELECT start_time AS "Start", workout_type AS "Workout",
       round((coalesce(duration_seconds, extract(epoch FROM end_time-start_time))/60)::numeric, 1) AS "Minutes",
       round(total_energy_kcal::numeric, 1) AS "Calories",
       round((distance_meters/1000)::numeric, 2) AS "Distance km", id
FROM workouts
WHERE $__timeFilter(start_time)
ORDER BY start_time DESC;

-- Daily workout volume
SELECT date_trunc('day', start_time AT TIME ZONE 'UTC') AS "time",
       count(*) AS "Workouts", sum(duration_seconds)/60 AS "Training minutes",
       sum(total_energy_kcal) AS "Workout calories"
FROM workouts
WHERE $__timeFilter(start_time)
GROUP BY 1 ORDER BY 1;

-- Metric sample summary; only SUM additive sample types after checking export semantics
SELECT time_bucket('1 day', time) AS "time", metric, unit,
       avg(value) AS average, min(value) AS minimum, max(value) AS maximum, count(*) AS samples
FROM health_metrics
WHERE $__timeFilter(time)
GROUP BY 1,2,3 ORDER BY 1,2;

-- Steps by day only if values are interval increments rather than cumulative totals
SELECT time_bucket('1 day', time) AS "time", sum(value) AS "Steps"
FROM health_metrics
WHERE $__timeFilter(time) AND metric IN ('steps','step_count','HKQuantityTypeIdentifierStepCount')
GROUP BY 1 ORDER BY 1;

-- Join health samples to workouts by time window, for selected workout UUID variable
SELECT hm.time AS "time", hm.value AS "Heart rate"
FROM health_metrics hm JOIN workouts w
  ON hm.time BETWEEN w.start_time AND w.end_time
WHERE w.id::text = '$workout_id'
  AND hm.metric IN ('heart_rate','HKQuantityTypeIdentifierHeartRate')
  AND $__timeFilter(hm.time)
ORDER BY hm.time;
