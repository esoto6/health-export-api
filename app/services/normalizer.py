"""Normalize Health Auto Export payloads into validated domain records."""

import itertools
import json
import logging
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.domain.schemas import (
    MetricIn,
    WorkoutIn,
    WorkoutIntervalIn,
    WorkoutRouteIn,
    WorkoutSampleIn,
)

logger = logging.getLogger(__name__)

ADDITIVE_METRICS = {
    "active_energy",
    "apple_exercise_time",
    "apple_stand_time",
    "basal_energy_burned",
    "cycling_distance",
    "dietary_water",
    "flights_climbed",
    "step_count",
    "time_in_daylight",
    "walking_running_distance",
}


def normalize_resolution(value: Any) -> str | None:
    """Normalize common export resolution labels to the stored vocabulary."""

    if value is None:
        return None
    normalized = str(value).strip().lower().replace("_", "-")
    aliases = {
        "raw": "raw",
        "sample": "raw",
        "samples": "raw",
        "day": "daily",
        "daily": "daily",
        "1-day": "daily",
        "week": "weekly",
        "weekly": "weekly",
        "7-day": "weekly",
        "month": "monthly",
        "monthly": "monthly",
        "1-month": "monthly",
    }
    return aliases.get(normalized)


def infer_resolution(metric: Any, samples: Any) -> str:
    """Infer additive export rollups from the cadence of a metric series."""

    if str(metric).lower() not in ADDITIVE_METRICS or not isinstance(samples, list):
        return "raw"

    timestamps = sorted(
        {
            parsed
            for item in samples
            if isinstance(item, dict)
            if (
                parsed := parse_time(
                    first(
                        item,
                        "start_time",
                        "startDate",
                        "start",
                        "date",
                        "timestamp",
                        "time",
                    )
                )
            )
            is not None
        }
    )
    if len(timestamps) < 3:
        return "raw"

    gaps = [
        (current - previous).total_seconds()
        for previous, current in itertools.pairwise(timestamps)
    ]
    weekly = sum(6.9 * 86400 <= gap <= 7.1 * 86400 for gap in gaps)
    daily = sum(0.9 * 86400 <= gap <= 1.1 * 86400 for gap in gaps)
    required = max(2, round(len(gaps) * 0.8))
    if weekly >= required:
        return "weekly"
    if daily >= required:
        return "daily"
    return "raw"


def parse_time(value: Any):
    """Parse supported timestamps and normalize them to UTC."""

    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value /= 1000
        return datetime.fromtimestamp(value, tz=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def first(obj, *keys):
    """Return the first non-null value for the requested mapping keys."""

    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return None


def flatten_samples(
    obj, inherited_metric=None, inherited_unit=None, inherited_resolution=None
):
    """Yield metric samples from nested and metric-keyed export structures."""

    if isinstance(obj, list):
        for item in obj:
            yield from flatten_samples(
                item,
                inherited_metric=inherited_metric,
                inherited_unit=inherited_unit,
                inherited_resolution=inherited_resolution,
            )
        return

    if not isinstance(obj, dict):
        return

    # Metric group fields may be on the parent object.
    metric = (
        first(obj, "metric", "name", "type", "HKType", "quantityType")
        or inherited_metric
    )
    unit = first(obj, "unit", "units", "_normalized_unit") or inherited_unit

    samples = first(obj, "samples", "data", "records", "values")
    resolution = normalize_resolution(
        first(obj, "resolution", "aggregation", "aggregationPeriod", "period")
    ) or normalize_resolution(inherited_resolution)
    if resolution is None and metric is not None:
        resolution = infer_resolution(metric, samples)
    value = first(obj, "value", "qty", "quantity", "average", "sum", "total")
    start = first(obj, "start_time", "startDate", "start", "date", "timestamp", "time")

    # Auto Health Export represents sleep as one summary object rather than a
    # regular quantity sample. Preserve the actual interval so nights crossing
    # midnight can be grouped by their local wake-up date in reporting.
    if (
        str(metric).lower() in {"sleep_analysis", "sleepanalysis"}
        and obj.get("totalSleep") is not None
        and obj.get("sleepStart") is not None
        and obj.get("sleepEnd") is not None
    ):
        yield {
            **obj,
            "_normalized_metric": "sleep_analysis",
            "_normalized_unit": unit or "hr",
            "value": obj["totalSleep"],
            "start_time": obj["sleepStart"],
            "end_time": obj["sleepEnd"],
            "sample_id": (
                f"sleep:{obj['sleepStart']}:{obj['sleepEnd']}:"
                f"{obj.get('source', 'unknown')}"
            ),
        }
        return

    # This object is an individual sample.
    if metric is not None and value is not None and start is not None:
        yield {
            **obj,
            "_normalized_metric": str(metric),
            "_normalized_unit": unit,
            "_normalized_resolution": resolution or "raw",
        }
        return

    # Recurse into the sample collection, carrying down the group metadata.
    if isinstance(samples, (list, dict)):
        yield from flatten_samples(
            samples,
            inherited_metric=str(metric) if metric else inherited_metric,
            inherited_unit=unit,
            inherited_resolution=resolution,
        )

    # Also support nested structures and metric-keyed objects.
    for key, child in obj.items():
        if key in {"samples", "data", "records", "values"}:
            continue

        if isinstance(child, (list, dict)):
            child_metric = str(metric) if metric else str(key)
            yield from flatten_samples(
                child,
                inherited_metric=child_metric,
                inherited_unit=unit,
                inherited_resolution=resolution,
            )


def normalize_metric(raw):
    """Convert one raw metric sample into a validated domain record."""

    metric = raw.get("_normalized_metric") or first(
        raw, "metric", "name", "type", "HKType", "quantityType"
    )
    value = first(raw, "value", "qty", "quantity", "average", "sum", "total")
    start = parse_time(
        first(raw, "start_time", "startDate", "start", "date", "timestamp", "time")
    )
    end = parse_time(first(raw, "end_time", "endDate", "end"))
    if metric is None or value is None or start is None:
        return None
    try:
        numeric = float(value)
    except (ValueError, TypeError):
        return None
    unit = first(raw, "unit", "units", "_normalized_unit") or "unknown"
    source = first(raw, "source", "sourceName", "source_name", "device") or "unknown"
    device = first(raw, "device", "deviceName")
    sid = first(raw, "sample_id", "sampleId", "id", "uuid", "HKUUID")
    reserved = {
        "_normalized_metric",
        "_normalized_resolution",
        "metric",
        "name",
        "type",
        "HKType",
        "quantityType",
        "value",
        "qty",
        "quantity",
        "average",
        "sum",
        "total",
        "unit",
        "units",
        "start_time",
        "startDate",
        "start",
        "date",
        "timestamp",
        "time",
        "end_time",
        "endDate",
        "end",
        "source",
        "sourceName",
        "source_name",
        "device",
        "deviceName",
        "sample_id",
        "sampleId",
        "id",
        "uuid",
        "HKUUID",
        "resolution",
        "aggregation",
        "aggregationPeriod",
        "period",
    }
    return MetricIn(
        metric=str(metric),
        value=numeric,
        unit=str(unit),
        start_time=start,
        end_time=end,
        source=str(source),
        device=str(device) if device else None,
        sample_id=str(sid) if sid else None,
        resolution=normalize_resolution(raw.get("_normalized_resolution")) or "raw",
        metadata={k: v for k, v in raw.items() if k not in reserved},
    )


def quantity_value(value):
    """Extract a numeric value from a scalar or Apple Health {qty, units} object."""

    if isinstance(value, dict):
        value = value.get("qty")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    return None


def numeric_value(value):
    """Return a finite-looking numeric scalar without accepting booleans."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_workout_routes(raw):
    """Validate and normalize the route points attached to a workout."""
    

    routes = raw.get("route")
    if not isinstance(routes, list):
        return []

    normalized = []
    mapped = {
        "timestamp",
        "latitude",
        "longitude",
        "altitude",
        "speed",
        "course",
        "horizontalAccuracy",
        "verticalAccuracy",
    }

    for sequence, point in enumerate(routes):
        if not isinstance(point, dict):
            continue

        recorded_at = parse_time(point.get("timestamp"))
        latitude = numeric_value(point.get("latitude"))
        longitude = numeric_value(point.get("longitude"))
        if recorded_at is None or latitude is None or longitude is None:
            logger.warning("Ignoring incomplete workout route point at %d", sequence)
            continue

        try:
            normalized.append(
                WorkoutRouteIn(
                    sequence=sequence,
                    recorded_at=recorded_at,
                    latitude=latitude,
                    longitude=longitude,
                    altitude_meters=numeric_value(point.get("altitude")),
                    speed=numeric_value(point.get("speed")),
                    course_degrees=numeric_value(point.get("course")),
                    horizontal_accuracy_meters=numeric_value(
                        point.get("horizontalAccuracy")
                    ),
                    vertical_accuracy_meters=numeric_value(
                        point.get("verticalAccuracy")
                    ),
                    metadata={
                        key: value for key, value in point.items() if key not in mapped
                    },
                )
            )
        except ValueError:
            logger.warning("Ignoring invalid workout route point at %d", sequence)

    return normalized


def normalize_workout_samples(raw, source_name):
    """Normalize workout, recovery, energy, distance, and step samples."""

    normalized = []

    def append_sample(item, phase, metric, value_key="qty"):
        """Append one valid child sample to the normalized collection."""

        if not isinstance(item, dict):
            return
        sample_time = parse_time(item.get("date"))
        value = numeric_value(item.get(value_key))
        if sample_time is None or value is None:
            return
        reserved = {"date", "units", "source", value_key}
        normalized.append(
            WorkoutSampleIn(
                time=sample_time,
                phase=phase,
                metric=metric,
                value=value,
                unit=str(item.get("units") or "unknown"),
                source=str(item.get("source") or source_name),
                metadata={key: val for key, val in item.items() if key not in reserved},
            )
        )

    for item in raw.get("heartRateData") or []:
        append_sample(item, "workout", "heart_rate_average", "Avg")
        append_sample(item, "workout", "heart_rate_minimum", "Min")
        append_sample(item, "workout", "heart_rate_maximum", "Max")

    for item in raw.get("heartRateRecovery") or []:
        append_sample(item, "recovery", "heart_rate_average", "Avg")
        append_sample(item, "recovery", "heart_rate_minimum", "Min")
        append_sample(item, "recovery", "heart_rate_maximum", "Max")

    series = {
        "activeEnergy": "active_energy",
        "basalEnergy": "basal_energy",
        "walkingAndRunningDistance": "walking_running_distance",
        "stepCount": "step_count",
    }
    for raw_key, metric in series.items():
        values = raw.get(raw_key)
        if not isinstance(values, list):
            continue
        for item in values:
            append_sample(item, "workout", metric)

    return normalized


def normalize_workout_intervals(raw):
    """Normalize workout segments and grouped distance splits."""

    normalized = []

    segments = raw.get("segments")
    if isinstance(segments, list):
        for sequence, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            reserved = {"start", "end", "activeDuration", "elapsedDuration", "metrics"}
            normalized.append(
                WorkoutIntervalIn(
                    interval_type="segment",
                    sequence=sequence,
                    start_time=parse_time(segment.get("start")),
                    end_time=parse_time(segment.get("end")),
                    duration_seconds=numeric_value(
                        first(segment, "activeDuration", "elapsedDuration")
                    ),
                    metrics=segment.get("metrics") or {},
                    metadata={
                        key: value
                        for key, value in segment.items()
                        if key not in reserved
                    },
                )
            )

    split_sequence = 0
    split_groups = raw.get("splits")
    if isinstance(split_groups, list):
        for group_sequence, group in enumerate(split_groups):
            if not isinstance(group, dict):
                continue
            target = group.get("targetDistance")
            target_value = quantity_value(target)
            target_unit = target.get("units") if isinstance(target, dict) else None
            items = group.get("items")
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                reserved = {
                    "start",
                    "end",
                    "activeDuration",
                    "elapsedDuration",
                    "metrics",
                }
                metadata = {
                    key: value for key, value in item.items() if key not in reserved
                }
                metadata.update(
                    {
                        "split_group": group_sequence,
                        "distance_type": group.get("distanceType"),
                        "group_boundary_origin": group.get("boundaryOrigin"),
                    }
                )
                normalized.append(
                    WorkoutIntervalIn(
                        interval_type="split",
                        sequence=split_sequence,
                        start_time=parse_time(item.get("start")),
                        end_time=parse_time(item.get("end")),
                        duration_seconds=numeric_value(
                            first(item, "activeDuration", "elapsedDuration")
                        ),
                        target_distance=target_value,
                        target_distance_unit=(
                            str(target_unit) if target_unit is not None else None
                        ),
                        metrics=item.get("metrics") or {},
                        metadata=metadata,
                    )
                )
                split_sequence += 1

    return normalized


def workout_summary_metrics(raw):
    """Extract workout-level heart rate, energy, and effort summaries."""

    heart_rate = raw.get("heartRate")
    if not isinstance(heart_rate, dict):
        heart_rate = {}

    # Some exports nest workout-level metrics inside activities.
    activity_metrics = {}
    activities = raw.get("activities")
    if isinstance(activities, list):
        for activity in activities:
            if not isinstance(activity, dict):
                continue
            metrics = activity.get("metrics")
            if isinstance(metrics, dict):
                activity_metrics.update(metrics)

    def first_quantity(*values):
        """Return the first value that contains a numeric quantity."""

        for value in values:
            result = quantity_value(value)
            if result is not None:
                return result
        return None

    return {
        "average_heart_rate_bpm": first_quantity(
            raw.get("avgHeartRate"),
            heart_rate.get("avg"),
            activity_metrics.get("heartRateAverage"),
        ),
        "max_heart_rate_bpm": first_quantity(
            raw.get("maxHeartRate"),
            heart_rate.get("max"),
            activity_metrics.get("heartRateMaximum"),
        ),
        "min_heart_rate_bpm": first_quantity(
            heart_rate.get("min"),
            activity_metrics.get("heartRateMinimum"),
        ),
        "active_energy_kcal": first_quantity(
            activity_metrics.get("activeEnergy"),
            raw.get("activeEnergyBurned"),
        ),
        "total_energy_kcal": first_quantity(
            raw.get("total_energy_kcal"),
            raw.get("totalEnergy"),
            raw.get("totalEnergyBurned"),
            raw.get("calories"),
        ),
        "effort_score": first_quantity(raw.get("effortScore")),
    }


def normalize_workout(raw):
    """Convert one raw workout and its children into a domain record."""

    start = parse_time(first(raw, "start_time", "startDate", "start", "startTime"))
    end = parse_time(first(raw, "end_time", "endDate", "end", "endTime"))

    if not start or not end:
        return None

    kind = (
        first(raw, "workout_type", "workoutActivityType", "activity", "type", "name")
        or "unknown"
    )

    sid = first(raw, "source_id", "id", "uuid", "HKUUID")
    if not sid:
        sid = sha256(
            f"{kind}|{start.isoformat()}|{end.isoformat()}".encode()
        ).hexdigest()

    def number(value):
        """Return the numeric quantity used by scalar workout fields."""

        return quantity_value(value)

    duration = first(raw, "duration_seconds", "duration")
    distance_raw = first(
        raw, "distance_miles", "distance_meters", "totalDistance", "distance"
    )

    distance_miles = None

    if isinstance(distance_raw, dict):
        qty = quantity_value(distance_raw)
        unit = str(distance_raw.get("units", "")).strip().lower()

        if qty is not None:
            if unit in {"mi", "mile", "miles"}:
                distance_miles = qty
            elif unit in {"m", "meter", "meters", "metre", "metres"}:
                distance_miles = qty / 1609.344
            elif unit in {"km", "kilometer", "kilometers", "kilometre", "kilometres"}:
                distance_miles = qty / 1.609344

    elif distance_raw is not None:
        try:
            qty = float(distance_raw)
            if first(raw, "distance_miles") is not None:
                distance_miles = qty
            elif first(raw, "distance_meters") is not None:
                distance_miles = qty / 1609.344
        except (ValueError, TypeError):
            logger.warning("Ignoring invalid workout distance")

    source_raw = first(raw, "source", "sourceName")
    if isinstance(source_raw, dict):
        source_name = source_raw.get("name") or "Apple Health"
        source_identifier = source_raw.get("identifier")
        source = source_name
    else:
        source_name = str(source_raw) if source_raw else "Apple Health"
        source = "Apple Health"
        source_identifier = None

    reserved = {
        "start_time",
        "startDate",
        "start",
        "startTime",
        "end_time",
        "endDate",
        "end",
        "endTime",
        "workout_type",
        "workoutActivityType",
        "activity",
        "type",
        "name",
        "source_id",
        "id",
        "uuid",
        "HKUUID",
        "duration_seconds",
        "duration",
        "total_energy_kcal",
        "totalEnergy",
        "totalEnergyBurned",
        "activeEnergyBurned",
        "calories",
        "distance_miles",
        "distance_meters",
        "totalDistance",
        "distance",
        "source",
        "sourceName",
        "route",
        "segments",
        "splits",
        "heartRateData",
        "heartRateRecovery",
        "activeEnergy",
        "basalEnergy",
        "walkingAndRunningDistance",
        "stepCount",
    }

    metadata = {key: value for key, value in raw.items() if key not in reserved}

    if source_identifier:
        metadata["source_identifier"] = source_identifier

    summary = workout_summary_metrics(raw)

    return WorkoutIn(
        source_id=str(sid),
        source=source,
        workout_type=str(kind),
        start_time=start,
        end_time=end,
        duration_seconds=number(duration),
        total_energy_kcal=summary["total_energy_kcal"],
        distance_miles=distance_miles,
        source_name=source_name,
        metadata=metadata,
        average_heart_rate_bpm=summary["average_heart_rate_bpm"],
        max_heart_rate_bpm=summary["max_heart_rate_bpm"],
        min_heart_rate_bpm=summary["min_heart_rate_bpm"],
        active_energy_kcal=summary["active_energy_kcal"],
        effort_score=summary["effort_score"],
        routes=normalize_workout_routes(raw),
        samples=normalize_workout_samples(raw, source_name),
        intervals=normalize_workout_intervals(raw),
    )


def extract_payload(payload):
    """Extract normalized metrics and workouts from supported payload shapes."""

    metric_objects: list[dict[str, Any]] = []
    workout_objects: list[dict[str, Any]] = []

    if isinstance(payload, list):
        metric_objects.extend(flatten_samples(payload))

    elif isinstance(payload, dict):
        # Apple Health exports may place workouts at the root
        # or under payload["data"]["workouts"].
        containers = [payload]
        data = payload.get("data")

        if isinstance(data, dict):
            containers.append(data)

        for container in containers:
            for key in ("workouts", "workout", "workoutData"):
                value = container.get(key)

                if isinstance(value, list):
                    workout_objects.extend(
                        item for item in value if isinstance(item, dict)
                    )
                elif isinstance(value, dict):
                    workout_objects.append(value)

        # Extract metrics without re-processing the workouts collection.
        for key in ("metrics", "samples", "records"):
            if key in payload:
                metric_objects.extend(flatten_samples(payload[key]))

        if "data" in payload:
            data = payload["data"]

            if isinstance(data, dict):
                metric_data = {
                    key: value
                    for key, value in data.items()
                    if key not in ("workouts", "workout", "workoutData")
                }
                if metric_data:
                    metric_objects.extend(flatten_samples(metric_data))
            elif isinstance(data, list):
                metric_objects.extend(flatten_samples(data))

        if first(payload, "start_time", "startDate", "timestamp", "date") is not None:
            metric_objects.extend(flatten_samples(payload))

    metrics = [
        normalized
        for raw in metric_objects
        if isinstance(raw, dict)
        if (normalized := normalize_metric(raw))
    ]

    workouts = []
    for raw in workout_objects:
        normalized = normalize_workout(raw)
        if normalized:
            workouts.append(normalized)

    return metrics, workouts


def canonical_json(value):
    """Serialize a value deterministically for hashing and identity keys."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
