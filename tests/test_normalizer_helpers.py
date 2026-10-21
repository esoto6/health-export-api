"""Focused tests for individual normalization helper functions."""

from datetime import UTC, datetime

import pytest

from app.services.normalizer import (
    canonical_json,
    extract_payload,
    first,
    flatten_samples,
    normalize_metric,
    normalize_workout,
    normalize_workout_intervals,
    normalize_workout_routes,
    normalize_workout_samples,
    numeric_value,
    parse_time,
    quantity_value,
    workout_summary_metrics,
)


def test_parse_time_supports_iso_naive_seconds_and_milliseconds():
    """Verify parsing of ISO, naive, second, and millisecond timestamps."""

    expected = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert parse_time("2026-10-15T12:00:00Z") == expected
    assert parse_time("2026-10-15T12:00:00") == expected
    assert parse_time(expected.timestamp()) == expected
    assert parse_time(expected.timestamp() * 1000) == expected


@pytest.mark.parametrize("value", [None, "", "not-a-date", {}, []])
def test_parse_time_rejects_invalid_values(value):
    """Verify that unsupported timestamp values are rejected."""

    assert parse_time(value) is None


def test_first_returns_first_non_null_value():
    """Verify selection of the first non-null mapping value."""

    assert first({"a": None, "b": 0, "c": 3}, "a", "b", "c") == 0
    assert first({}, "missing") is None


def test_flatten_samples_supports_grouped_and_metric_keyed_data():
    """Verify flattening of grouped and metric-keyed sample structures."""

    grouped = {
        "name": "heart_rate",
        "units": "bpm",
        "data": [{"date": "2026-10-15T12:00:00Z", "qty": 72}],
    }
    metric_keyed = {"step_count": [{"date": "2026-10-15T12:00:00Z", "qty": 50}]}

    assert next(flatten_samples(grouped))["_normalized_metric"] == "heart_rate"
    assert next(flatten_samples(metric_keyed))["_normalized_metric"] == "step_count"
    assert list(flatten_samples("not-an-object")) == []


def test_normalize_metric_preserves_metadata_and_rejects_bad_samples():
    """Verify metric metadata preservation and invalid-sample rejection."""

    metric = normalize_metric(
        {
            "metric": "heart_rate",
            "value": "72.5",
            "unit": "bpm",
            "date": "2026-10-15T12:00:00Z",
            "sourceName": "Fixture Watch",
            "deviceName": "Fixture Device",
            "custom": "kept",
        }
    )

    assert metric.value == 72.5
    assert metric.source == "Fixture Watch"
    assert metric.device == "Fixture Device"
    assert metric.metadata == {"custom": "kept"}
    assert (
        normalize_metric({"metric": "x", "value": "bad", "date": "2026-10-15"}) is None
    )
    assert normalize_metric({"metric": "x", "value": 1}) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [({"qty": 2}, 2.0), (3, 3.0), (True, None), ("3", None), (None, None)],
)
def test_quantity_value(value, expected):
    """Verify numeric extraction from scalar and quantity objects."""

    assert quantity_value(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, 3.0), ("3.5", 3.5), (True, None), (None, None), ("bad", None)],
)
def test_numeric_value(value, expected):
    """Verify safe numeric conversion without accepting booleans."""

    assert numeric_value(value) == expected


def test_normalize_routes_filters_incomplete_and_invalid_points():
    """Verify route normalization filters incomplete and invalid points."""

    routes = normalize_workout_routes(
        {
            "route": [
                {
                    "timestamp": "2026-10-15T12:00:00Z",
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "note": "kept",
                },
                {"timestamp": "bad", "latitude": 1, "longitude": 2},
                {
                    "timestamp": "2026-10-15T12:01:00Z",
                    "latitude": 200,
                    "longitude": 2,
                },
                "not-a-point",
            ]
        }
    )

    assert len(routes) == 1
    assert routes[0].sequence == 0
    assert routes[0].metadata == {"note": "kept"}
    assert normalize_workout_routes({"route": {}}) == []


def test_normalize_workout_samples_covers_workout_recovery_and_series():
    """Verify workout, recovery, and auxiliary series normalization."""

    raw = {
        "heartRateData": [
            {"date": "2026-10-15T12:00:00Z", "Avg": 90, "Min": 70, "Max": 110}
        ],
        "heartRateRecovery": [
            {"date": "2026-10-15T12:31:00Z", "Avg": 80, "Min": 70, "Max": 90}
        ],
        "stepCount": [{"date": "2026-10-15T12:10:00Z", "qty": 500}],
        "activeEnergy": ["ignored", {"date": "bad", "qty": 1}],
    }

    samples = normalize_workout_samples(raw, "Fixture Watch")

    assert len(samples) == 7
    assert {sample.phase for sample in samples} == {"workout", "recovery"}
    assert samples[-1].metric == "step_count"
    assert all(sample.source == "Fixture Watch" for sample in samples)


def test_normalize_workout_intervals_handles_segments_and_split_groups():
    """Verify segment and grouped split normalization."""

    intervals = normalize_workout_intervals(
        {
            "segments": [
                {
                    "start": "2026-10-15T12:00:00Z",
                    "end": "2026-10-15T12:05:00Z",
                    "activeDuration": 300,
                    "metrics": {"steps": 500},
                    "label": "warmup",
                },
                "ignored",
            ],
            "splits": [
                {
                    "targetDistance": {"qty": 1, "units": "km"},
                    "distanceType": "kilometer",
                    "items": [
                        {
                            "start": "2026-10-15T12:00:00Z",
                            "elapsedDuration": 600,
                            "metrics": {"pace": 10},
                        }
                    ],
                },
                {"items": "ignored"},
            ],
        }
    )

    assert [item.interval_type for item in intervals] == ["segment", "split"]
    assert intervals[0].metadata == {"label": "warmup"}
    assert intervals[1].target_distance == 1
    assert intervals[1].target_distance_unit == "km"
    assert intervals[1].metadata["split_group"] == 0


def test_workout_summary_metrics_uses_activity_and_top_level_fallbacks():
    """Verify workout summaries use nested and top-level fallbacks."""

    summary = workout_summary_metrics(
        {
            "heartRate": {"avg": {"qty": 90}, "min": {"qty": 70}},
            "maxHeartRate": {"qty": 115},
            "activeEnergyBurned": {"qty": 100},
            "totalEnergy": {"qty": 150},
            "effortScore": {"qty": 3},
            "activities": [{"metrics": {"heartRateMaximum": {"qty": 120}}}, "ignored"],
        }
    )

    assert summary == {
        "average_heart_rate_bpm": 90,
        "max_heart_rate_bpm": 115,
        "min_heart_rate_bpm": 70,
        "active_energy_kcal": 100,
        "total_energy_kcal": 150,
        "effort_score": 3,
    }


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        ({"qty": 1, "units": "mi"}, 1),
        ({"qty": 1609.344, "units": "m"}, 1),
        ({"qty": 1.609344, "units": "km"}, 1),
    ],
)
def test_normalize_workout_converts_distance_units(distance, expected):
    """Verify conversion of supported workout distance units to miles."""

    workout = normalize_workout(
        {
            "workoutActivityType": "Walk",
            "startDate": "2026-10-15T12:00:00Z",
            "endDate": "2026-10-15T12:30:00Z",
            "distance": distance,
        }
    )

    assert workout.distance_miles == pytest.approx(expected)
    assert len(workout.source_id) == 64


def test_normalize_workout_rejects_missing_times_and_handles_scalar_distance():
    """Verify missing-time rejection and scalar distance handling."""

    assert normalize_workout({"startDate": "2026-10-15T12:00:00Z"}) is None

    workout = normalize_workout(
        {
            "id": "w1",
            "type": "Walk",
            "startDate": "2026-10-15T12:00:00Z",
            "endDate": "2026-10-15T12:30:00Z",
            "distance_meters": 1609.344,
            "source": "Fixture Phone",
        }
    )
    assert workout.distance_miles == pytest.approx(1)
    assert workout.source == "Apple Health"
    assert workout.source_name == "Fixture Phone"


def test_fixture_payloads_cover_metrics_and_nested_export(load_fixture):
    """Verify synthetic fixtures cover metric and nested export shapes."""

    metrics, workouts = extract_payload(load_fixture("metrics.json"))
    assert [metric.metric for metric in metrics] == [
        "heart_rate",
        "step_count",
        "sleep_analysis",
    ]
    assert workouts == []

    metrics, workouts = extract_payload(load_fixture("auto-health-export.json"))
    assert len(metrics) == 1
    assert len(workouts) == 1
    assert workouts[0].routes[0].latitude == 0.0


def test_extract_payload_supports_list_and_single_sample_shapes():
    """Verify extraction from list and single-sample payloads."""

    sample = {"metric": "steps", "value": 10, "date": "2026-10-15T12:00:00Z"}
    metrics, workouts = extract_payload([sample])
    assert len(metrics) == 1 and workouts == []

    metrics, workouts = extract_payload(sample)
    assert len(metrics) == 1 and workouts == []


def test_canonical_json_is_stable_and_compact():
    """Verify canonical JSON is deterministic and compact."""

    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
