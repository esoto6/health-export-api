"""Tests for metric and workout persistence helpers."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.domain.schemas import MetricIn, WorkoutIn
from app.services import persistence
from app.services.normalizer import extract_payload
from app.services.persistence import (
    commit_or_rollback,
    metric_key,
    model_dict,
    save_metrics,
    save_workouts,
)

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class Result:
    """Provide the minimal SQLAlchemy result behavior used by tests."""

    def __init__(self, rows=()):
        """Store rows returned by the fake database execution."""

        self.rows = list(rows)

    def all(self):
        """Return all stored rows."""

        return self.rows

    def fetchall(self):
        """Return all stored rows through the cursor-style API."""

        return self.rows

    def __iter__(self):
        """Iterate over stored rows."""

        return iter(self.rows)


def metric(index=0, sample_id=None):
    """Build a deterministic metric domain object for persistence tests."""

    return MetricIn(
        metric="step_count",
        value=index + 1,
        unit="count",
        start_time=NOW + timedelta(seconds=index),
        source="Fixture Phone",
        sample_id=sample_id,
        metadata={"index": index},
    )


def workout(source_id="w1"):
    """Build a deterministic workout domain object for persistence tests."""

    return WorkoutIn(
        source_id=source_id,
        workout_type="Walk",
        start_time=NOW,
        end_time=NOW + timedelta(minutes=30),
    )


def test_model_dict_supports_models_and_dicts():
    """Verify conversion of models and dictionaries to plain mappings."""

    item = metric()
    assert model_dict(item)["metric"] == "step_count"
    assert model_dict({"metric": "steps"}) == {"metric": "steps"}

    with pytest.raises(TypeError, match="Pydantic model or dict"):
        model_dict(object())


def test_metric_key_is_stable_user_specific_and_uses_sample_id():
    """Verify metric keys are stable, user-scoped, and sample-aware."""

    item = metric(sample_id="sample-1")
    assert metric_key(item, USER_ID) == metric_key(item, USER_ID)
    assert metric_key(item, USER_ID) != metric_key(
        item, UUID("22222222-2222-4222-8222-222222222222")
    )
    assert metric_key(metric(), USER_ID) == metric_key(metric(), USER_ID)


def test_save_metrics_handles_empty_input(monkeypatch):
    """Verify empty metric batches produce no database writes."""

    db = MagicMock()
    event = MagicMock()
    monkeypatch.setattr(persistence, "log_event", event)
    assert save_metrics(db, [], USER_ID) == 0
    db.execute.assert_not_called()
    assert event.call_args.kwargs["user_id"] == str(USER_ID)
    assert event.call_args.kwargs["upload_type"] == "metrics"


def test_save_metrics_batches_rows_and_counts_inserted():
    """Verify metric writes are batched and inserted rows are counted."""

    db = MagicMock()
    db.execute.side_effect = [Result([object()] * 499), Result([object()])]
    items = [metric(index) for index in range(501)]

    assert save_metrics(db, items, USER_ID) == 500
    assert db.execute.call_count == 2
    first_batch = db.execute.call_args_list[0].args[1]
    second_batch = db.execute.call_args_list[1].args[1]
    assert len(first_batch) == 500
    assert len(second_batch) == 1
    assert first_batch[0]["metadata_json"] == {"index": 0}
    assert first_batch[0]["resolution"] == "raw"


def test_save_metrics_propagates_database_errors():
    """Verify metric persistence propagates database failures."""

    db = MagicMock()
    db.execute.side_effect = RuntimeError("insert failed")

    with pytest.raises(RuntimeError, match="insert failed"):
        save_metrics(db, [metric()], USER_ID)


def test_save_workouts_handles_empty_input(monkeypatch):
    """Verify empty workout batches produce no database writes."""

    db = MagicMock()
    event = MagicMock()
    monkeypatch.setattr(persistence, "log_event", event)
    assert save_workouts(db, [], USER_ID) == 0
    db.execute.assert_not_called()
    assert event.call_args.kwargs["user_id"] == str(USER_ID)
    assert event.call_args.kwargs["upload_type"] == "workouts"


def test_save_workouts_upserts_parent_and_replaces_all_children(load_fixture):
    """Verify workout upserts replace every normalized child collection."""

    _, workouts = extract_payload(load_fixture("workout.json"))
    item = workouts[0]
    workout_id = UUID("33333333-3333-4333-8333-333333333333")
    call_number = 0

    def execute(*_args, **_kwargs):
        """Return staged fake results for successive database calls."""

        nonlocal call_number
        call_number += 1
        if call_number == 1:
            return Result()
        if call_number == 2:
            return Result(
                [
                    SimpleNamespace(
                        id=workout_id,
                        source=item.source,
                        source_id=item.source_id,
                    )
                ]
            )
        return Result()

    db = MagicMock()
    db.execute.side_effect = execute

    assert save_workouts(db, [item], USER_ID) == 1
    assert db.execute.call_count == 8

    parameter_batches = [
        call.args[1]
        for call in db.execute.call_args_list
        if len(call.args) > 1 and isinstance(call.args[1], list)
    ]
    assert [len(batch) for batch in parameter_batches] == [1, 7, 2]
    assert parameter_batches[0][0]["latitude"] == 0.0
    assert parameter_batches[1][0]["metric"] == "heart_rate_average"
    assert parameter_batches[2][0]["interval_type"] == "segment"


def test_save_workouts_reports_existing_workout_as_not_inserted():
    """Verify an existing workout is counted as an update."""

    item = workout()
    workout_id = UUID("33333333-3333-4333-8333-333333333333")
    db = MagicMock()
    db.execute.side_effect = [
        Result([(item.source, item.source_id)]),
        Result(
            [
                SimpleNamespace(
                    id=workout_id,
                    source=item.source,
                    source_id=item.source_id,
                )
            ]
        ),
    ]

    assert save_workouts(db, [item], USER_ID) == 0


def test_commit_or_rollback_commits_successfully():
    """Verify successful transaction commits are not rolled back."""

    db = MagicMock()
    commit_or_rollback(db)
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_commit_or_rollback_rolls_back_and_reraises():
    """Verify failed commits are rolled back and re-raised."""

    db = MagicMock()
    db.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        commit_or_rollback(db)

    db.rollback.assert_called_once_with()
