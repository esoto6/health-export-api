"""Tests for ingestion endpoints, transactions, and structured events."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.api.routes import ingestion
from app.domain.schemas import MetricIn, WorkoutIn


def test_ingestion_summary_helpers():
    """Verify privacy-safe upload and workout summary helpers."""

    metrics = [
        SimpleNamespace(metric="steps"),
        SimpleNamespace(metric="steps"),
        SimpleNamespace(metric="heart_rate"),
    ]
    workout = SimpleNamespace(routes=[1, 2], samples=[1], intervals=None)

    assert ingestion.summarize_metrics(metrics) == {"metric_types": 2}
    assert ingestion.workout_detail_count([workout]) == 3
    assert ingestion.workout_detail_summary([workout]) == {
        "route_points": 2,
        "workout_samples": 1,
        "workout_intervals": 0,
    }
    assert ingestion.upload_type(metrics, [workout]) == "mixed"
    assert ingestion.upload_type(metrics, []) == "metrics"
    assert ingestion.upload_type([], [workout]) == "workouts"
    assert ingestion.upload_type([], []) == "empty"


def test_user_log_context_excludes_email(test_user):
    """Verify that log identity context excludes user email addresses."""

    assert ingestion.user_log_context(test_user) == {
        "user_id": str(test_user.id),
        "user_name": "Fixture User",
    }


def test_log_stage_emits_duration(monkeypatch):
    """Verify that stage logging records elapsed processing time."""

    event = MagicMock()
    monkeypatch.setattr(ingestion, "log_event", event)
    monkeypatch.setattr(ingestion, "perf_counter", lambda: 2.0)

    ingestion.log_stage("normalize", 1.25, count=3)

    event.assert_called_once_with(
        ingestion.logger,
        "processing.stage.completed",
        stage="normalize",
        duration_ms=750.0,
        count=3,
    )


def test_ingest_metrics_commits_and_reports_counts(monkeypatch, test_user):
    """Verify successful metric ingestion counts and transaction commit."""

    db = MagicMock()
    items = [
        MetricIn(
            metric="steps",
            value=10,
            unit="count",
            start_time="2026-10-15T12:00:00Z",
        ),
        MetricIn(
            metric="steps",
            value=20,
            unit="count",
            start_time="2026-10-15T12:01:00Z",
        ),
    ]
    monkeypatch.setattr(ingestion, "save_metrics", lambda *_: 1)
    event = MagicMock()
    monkeypatch.setattr(ingestion, "log_event", event)
    commit = MagicMock()
    monkeypatch.setattr(ingestion, "commit_or_rollback", commit)

    result = ingestion.ingest_metrics(items, test_user, db)

    assert result == {"received": 2, "inserted": 1, "skipped": 1}
    commit.assert_called_once_with(db)
    completed = event.call_args.kwargs
    assert completed["user_id"] == str(test_user.id)
    assert completed["user_name"] == test_user.name
    assert completed["upload_type"] == "metrics"


def test_ingest_metrics_rolls_back_on_failure(monkeypatch, test_user):
    """Verify metric ingestion rolls back when persistence fails."""

    db = MagicMock()
    monkeypatch.setattr(
        ingestion, "save_metrics", MagicMock(side_effect=RuntimeError("write failed"))
    )

    with pytest.raises(RuntimeError, match="write failed"):
        ingestion.ingest_metrics([], test_user, db)

    db.rollback.assert_called_once_with()


def test_ingest_workouts_commits_and_reports_details(monkeypatch, test_user):
    """Verify successful workout ingestion reports child-record counts."""

    db = MagicMock()
    workout = WorkoutIn(
        source_id="w1",
        workout_type="Walk",
        start_time="2026-10-15T12:00:00Z",
        end_time="2026-10-15T12:30:00Z",
        routes=[],
        samples=[],
        intervals=[],
    )
    monkeypatch.setattr(ingestion, "save_workouts", lambda *_: 1)
    event = MagicMock()
    monkeypatch.setattr(ingestion, "log_event", event)
    commit = MagicMock()
    monkeypatch.setattr(ingestion, "commit_or_rollback", commit)

    result = ingestion.ingest_workouts([workout], test_user, db)

    assert result == {"received": 1, "inserted": 1, "skipped": 0}
    commit.assert_called_once_with(db)
    completed = event.call_args.kwargs
    assert completed["user_id"] == str(test_user.id)
    assert completed["user_name"] == test_user.name
    assert completed["upload_type"] == "workouts"


def test_ingest_workouts_rolls_back_on_failure(monkeypatch, test_user):
    """Verify workout ingestion rolls back when persistence fails."""

    db = MagicMock()
    monkeypatch.setattr(
        ingestion, "save_workouts", MagicMock(side_effect=RuntimeError("write failed"))
    )

    with pytest.raises(RuntimeError, match="write failed"):
        ingestion.ingest_workouts([], test_user, db)

    db.rollback.assert_called_once_with()


def test_auto_health_export_returns_existing_batch(load_fixture, test_user):
    """Verify that duplicate exports return the recorded batch result."""

    batch_id = UUID("44444444-4444-4444-8444-444444444444")
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        id=batch_id,
        item_count=3,
        inserted_count=2,
        skipped_count=1,
    )

    payload = load_fixture("auto-health-export.json")
    result = ingestion.ingest_auto_health_export(payload, test_user, db)

    assert result == {
        "status": "duplicate",
        "batch_id": str(batch_id),
        "received": 3,
        "inserted": 2,
        "skipped": 1,
    }
    db.commit.assert_not_called()


def test_auto_health_export_logs_mixed_upload(monkeypatch, load_fixture, test_user):
    """Verify mixed exports emit privacy-safe structured events."""

    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        id=UUID("44444444-4444-4444-8444-444444444444"),
        item_count=2,
        inserted_count=2,
        skipped_count=0,
    )
    event = MagicMock()
    monkeypatch.setattr(ingestion, "log_event", event)

    ingestion.ingest_auto_health_export(
        load_fixture("auto-health-export.json"), test_user, db
    )

    duplicate = next(
        call for call in event.call_args_list if call.args[1] == "ingestion.duplicate"
    )
    assert duplicate.kwargs["upload_type"] == "mixed"
    assert duplicate.kwargs["user_id"] == str(test_user.id)


def test_auto_health_export_normalizes_persists_and_records_batch(
    monkeypatch, load_fixture, test_user
):
    """Verify complete export normalization, persistence, and batch tracking."""

    batch_id = UUID("44444444-4444-4444-8444-444444444444")
    db = MagicMock()
    db.scalar.return_value = None
    monkeypatch.setattr(ingestion, "save_metrics", lambda *_: 1)
    monkeypatch.setattr(ingestion, "save_workouts", lambda *_: 1)

    def refresh(batch):
        """Assign a deterministic identifier to the new batch record."""

        batch.id = batch_id

    db.refresh.side_effect = refresh

    result = ingestion.ingest_auto_health_export(
        load_fixture("auto-health-export.json"), test_user, db
    )

    assert result == {
        "status": "success",
        "batch_id": str(batch_id),
        "received": 2,
        "inserted": 2,
        "skipped": 0,
        "metrics_inserted": 1,
        "workouts_inserted": 1,
    }
    batch = db.add.call_args.args[0]
    assert batch.user_id == test_user.id
    assert batch.item_count == 2
    assert len(batch.payload_sha256) == 64
    db.commit.assert_called_once_with()


def test_auto_health_export_rolls_back_on_duplicate_check_error(
    load_fixture, test_user
):
    """Verify duplicate-check failures roll back the transaction."""

    db = MagicMock()
    db.scalar.side_effect = RuntimeError("read failed")

    with pytest.raises(RuntimeError, match="read failed"):
        ingestion.ingest_auto_health_export(
            load_fixture("auto-health-export.json"), test_user, db
        )

    db.rollback.assert_called_once_with()


def test_auto_health_export_rolls_back_on_database_error(
    monkeypatch, load_fixture, test_user
):
    """Verify export persistence failures roll back the transaction."""

    db = MagicMock()
    db.scalar.return_value = None
    monkeypatch.setattr(
        ingestion, "save_metrics", MagicMock(side_effect=RuntimeError("write failed"))
    )

    with pytest.raises(RuntimeError, match="write failed"):
        ingestion.ingest_auto_health_export(
            load_fixture("auto-health-export.json"), test_user, db
        )

    db.rollback.assert_called_once_with()
