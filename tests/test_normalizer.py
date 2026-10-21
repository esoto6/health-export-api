"""Integration tests for metric and workout payload normalization."""

import json
from pathlib import Path

from app.services.normalizer import extract_payload

FIXTURES = Path(__file__).parent / "fixtures"


def test_metrics():
    """Verify normalization of a basic metric payload."""

    payload = {
        "metrics": [
            {
                "name": "heart_rate",
                "data": [
                    {"date": "2026-10-15T12:00:00Z", "qty": 120, "units": "count/min"}
                ],
            }
        ]
    }
    metrics, workouts = extract_payload(payload)
    assert len(metrics) == 1
    assert metrics[0].metric == "heart_rate"
    assert metrics[0].value == 120
    assert workouts == []


def test_additive_metric_series_records_resolution():
    """Verify cadence-based resolution inference for additive metrics."""

    payload = {
        "metrics": [
            {
                "name": "step_count",
                "units": "count",
                "data": [
                    {"date": "2026-10-01 00:00:00 -0400", "qty": 70000},
                    {"date": "2026-10-08 00:00:00 -0400", "qty": 71000},
                    {"date": "2026-10-15 00:00:00 -0400", "qty": 72000},
                ],
            },
            {
                "name": "dietary_water",
                "units": "fl_oz_us",
                "resolution": "daily",
                "data": [
                    {"date": "2026-10-15 12:00:00 -0400", "qty": 128},
                ],
            },
        ]
    }

    metrics, _ = extract_payload(payload)

    assert [metric.resolution for metric in metrics[:3]] == ["weekly"] * 3
    assert metrics[3].resolution == "daily"


def test_nested_data_container_does_not_override_inferred_resolution():
    """Verify nested containers preserve inferred metric resolution."""

    payload = {
        "data": {
            "metrics": [
                {
                    "name": "active_energy",
                    "units": "kcal",
                    "data": [
                        {"date": "2026-10-01 00:00:00 -0400", "qty": 7000},
                        {"date": "2026-10-08 00:00:00 -0400", "qty": 7100},
                        {"date": "2026-10-15 00:00:00 -0400", "qty": 7200},
                    ],
                }
            ]
        }
    }

    metrics, _ = extract_payload(payload)

    assert [metric.resolution for metric in metrics] == ["weekly"] * 3


def test_workouts():
    """Verify normalization of a basic workout payload."""

    payload = {
        "workouts": [
            {
                "id": "w1",
                "workoutActivityType": "Running",
                "startDate": "2026-10-15T12:00:00Z",
                "endDate": "2026-10-15T12:30:00Z",
                "duration": 1800,
            }
        ]
    }
    _metrics, workouts = extract_payload(payload)
    assert len(workouts) == 1
    assert workouts[0].source_id == "w1"


def test_sleep_analysis_preserves_cross_midnight_interval():
    """Verify sleep summaries retain their cross-midnight interval."""

    payload = {
        "metrics": [
            {
                "name": "sleep_analysis",
                "units": "hr",
                "data": [
                    {
                        "source": "Apple Watch",
                        "date": "2026-10-14 00:00:00 -0400",
                        "sleepStart": "2026-10-13 21:59:13 -0400",
                        "sleepEnd": "2026-10-14 05:01:59 -0400",
                        "inBedStart": "2026-10-13 21:59:13 -0400",
                        "inBedEnd": "2026-10-14 05:01:59 -0400",
                        "totalSleep": 6.85,
                        "awake": 0.19,
                        "core": 4.87,
                        "deep": 0.72,
                        "rem": 1.26,
                    }
                ],
            }
        ]
    }

    metrics, workouts = extract_payload(payload)

    assert workouts == []
    assert len(metrics) == 1
    sleep = metrics[0]
    assert sleep.metric == "sleep_analysis"
    assert sleep.value == 6.85
    assert sleep.unit == "hr"
    assert sleep.start_time.isoformat() == "2026-10-14T01:59:13+00:00"
    assert sleep.end_time.isoformat() == "2026-10-14T09:01:59+00:00"
    assert sleep.metadata["deep"] == 0.72
    assert sleep.metadata["rem"] == 1.26


def test_auto_health_export_workout_details():
    """Verify normalization of detailed synthetic workout data."""

    with (FIXTURES / "workout.json").open() as fixture:
        payload = json.load(fixture)

    metrics, workouts = extract_payload(payload)

    assert metrics == []
    assert len(workouts) == 1

    walk = workouts[0]
    assert walk.source == "Test Watch"
    assert walk.source_name == "Test Watch"
    assert len(walk.routes) == 1
    assert len(walk.samples) == 7
    assert len(walk.intervals) == 2

    first_route = walk.routes[0]
    assert first_route.sequence == 0
    assert first_route.latitude == 0.0
    assert first_route.longitude == 0.0
    assert first_route.altitude_meters == 0.0
    assert first_route.metadata["speedAccuracy"] == 0.0

    phases = {sample.phase for sample in walk.samples}
    assert phases == {"workout", "recovery"}
    assert {interval.interval_type for interval in walk.intervals} == {
        "segment",
        "split",
    }
    assert [
        interval.sequence
        for interval in walk.intervals
        if interval.interval_type == "split"
    ] == [0]

    assert "route" not in walk.metadata
    assert "heartRateData" not in walk.metadata
    assert walk.metadata["location"] == "Outdoor"
