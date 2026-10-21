"""Tests for administrative and authenticated read routes."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import admin, health_data
from app.database.models import HealthMetric, User, Workout

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def test_create_api_key_for_existing_user(monkeypatch, test_user):
    """Verify key creation reuses an existing normalized user."""

    db = MagicMock()
    db.scalar.return_value = test_user
    key_id = UUID("22222222-2222-4222-8222-222222222222")

    def refresh(api_key):
        """Assign a deterministic identifier to the new key."""

        api_key.id = key_id

    db.refresh.side_effect = refresh
    monkeypatch.setattr(admin.secrets, "token_urlsafe", lambda _: "fixture-raw-key")

    result = admin.create_api_key(
        admin.CreateApiKeyRequest(
            name=" Ignored Name ",
            email=" FIXTURE@EXAMPLE.TEST ",
            key_name=" Phone ",
        ),
        db,
    )

    assert result["api_key"] == "fixture-raw-key"
    assert result["key_id"] == str(key_id)
    assert result["key_name"] == "Phone"
    assert result["email"] == "fixture@example.test"
    db.commit.assert_called_once_with()


def test_create_api_key_creates_user_and_normalizes_identity(monkeypatch):
    """Verify key creation adds a user with normalized identity fields."""

    db = MagicMock()
    db.scalar.return_value = None
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    key_id = UUID("22222222-2222-4222-8222-222222222222")

    def flush():
        """Assign a deterministic identifier to the pending user."""

        added_user = db.add.call_args_list[0].args[0]
        added_user.id = user_id

    def refresh(api_key):
        """Assign a deterministic identifier to the new key."""

        api_key.id = key_id

    db.flush.side_effect = flush
    db.refresh.side_effect = refresh
    monkeypatch.setattr(admin.secrets, "token_urlsafe", lambda _: "fixture-raw-key")

    result = admin.create_api_key(
        admin.CreateApiKeyRequest(
            name=" Fixture User ",
            email=" NEW@EXAMPLE.TEST ",
        ),
        db,
    )

    created = db.add.call_args_list[0].args[0]
    assert isinstance(created, User)
    assert created.name == "Fixture User"
    assert created.email == "new@example.test"
    assert result["user_id"] == str(user_id)


def test_create_api_key_request_rejects_removed_authentik_subject():
    """Verify the removed Authentik subject field is forbidden."""

    with pytest.raises(ValidationError, match="authentik_sub"):
        admin.CreateApiKeyRequest(
            name="Fixture",
            email="fixture@example.test",
            authentik_sub="removed-subject",
        )


def test_create_api_key_rolls_back_when_commit_fails(monkeypatch, test_user):
    """Verify key creation rolls back when commit fails."""

    db = MagicMock()
    db.scalar.return_value = test_user
    db.commit.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(admin.secrets, "token_urlsafe", lambda _: "fixture-key")

    with pytest.raises(RuntimeError, match="database unavailable"):
        admin.create_api_key(
            admin.CreateApiKeyRequest(name="Fixture", email=test_user.email), db
        )

    db.rollback.assert_called_once_with()


def test_get_metrics_serializes_records(test_user):
    """Verify metric records are serialized for API responses."""

    metric = HealthMetric(
        user_id=test_user.id,
        time=NOW,
        sample_key="k" * 64,
        metric="heart_rate",
        value=72,
        unit="bpm",
        end_time=None,
        source="Fixture Watch",
        device="Fixture Device",
        metadata_json={"fixture": True},
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [metric]

    result = health_data.get_metrics(limit=25, user=test_user, db=db)

    assert result == [
        {
            "time": NOW,
            "metric": "heart_rate",
            "value": 72,
            "unit": "bpm",
            "end_time": None,
            "source": "Fixture Watch",
            "device": "Fixture Device",
            "metadata": {"fixture": True},
        }
    ]


def make_workout(test_user):
    """Build a deterministic workout database record for route tests."""

    return Workout(
        id=UUID("33333333-3333-4333-8333-333333333333"),
        user_id=test_user.id,
        source_id="fixture-workout",
        source="Apple Health",
        workout_type="Outdoor Walk",
        start_time=NOW,
        end_time=datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
        duration_seconds=1800,
        total_energy_kcal=100,
        active_energy_kcal=75,
        distance_miles=1.5,
        average_heart_rate_bpm=92,
        max_heart_rate_bpm=110,
        min_heart_rate_bpm=75,
        effort_score=2,
        source_name="Fixture Watch",
        metadata_json={"location": "Outdoor"},
    )


def test_workout_response_and_collection(test_user):
    """Verify workout serialization and collection responses."""

    workout = make_workout(test_user)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [workout]

    result = health_data.get_workouts(limit=10, user=test_user, db=db)

    assert result[0] == health_data.workout_response(workout)
    assert result[0]["id"] == str(workout.id)
    assert result[0]["distance_miles"] == 1.5


def test_get_workout_returns_owned_workout_or_404(test_user):
    """Verify owned workout retrieval and not-found behavior."""

    workout = make_workout(test_user)
    db = MagicMock()
    db.scalar.return_value = workout

    assert (
        health_data.get_workout(workout.id, test_user, db)["source_id"]
        == "fixture-workout"
    )

    db.scalar.return_value = None
    with pytest.raises(HTTPException) as exc:
        health_data.get_workout(workout.id, test_user, db)
    assert exc.value.status_code == 404
