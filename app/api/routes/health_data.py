"""Authenticated read routes for normalized health and workout data."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import HealthMetric, User, Workout
from app.database.session import get_db
from app.security.auth import get_current_user

router = APIRouter(prefix="/api/v1", tags=["health data"])


@router.get("/metrics")
def get_metrics(
    limit: int = 1000,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the current user's most recent health metrics."""

    limit = max(1, min(limit, 10000))
    records = db.scalars(
        select(HealthMetric)
        .where(HealthMetric.user_id == user.id)
        .order_by(HealthMetric.time.desc())
        .limit(limit)
    ).all()
    return [
        {
            "time": item.time,
            "metric": item.metric,
            "value": item.value,
            "unit": item.unit,
            "end_time": item.end_time,
            "source": item.source,
            "device": item.device,
            "metadata": item.metadata_json,
        }
        for item in records
    ]


def workout_response(item: Workout) -> dict[str, Any]:
    """Serialize a workout database record for an API response."""

    return {
        "id": str(item.id),
        "source_id": item.source_id,
        "source": item.source,
        "workout_type": item.workout_type,
        "start_time": item.start_time,
        "end_time": item.end_time,
        "duration_seconds": item.duration_seconds,
        "total_energy_kcal": item.total_energy_kcal,
        "active_energy_kcal": item.active_energy_kcal,
        "distance_miles": item.distance_miles,
        "average_heart_rate_bpm": item.average_heart_rate_bpm,
        "max_heart_rate_bpm": item.max_heart_rate_bpm,
        "min_heart_rate_bpm": item.min_heart_rate_bpm,
        "effort_score": item.effort_score,
        "source_name": item.source_name,
        "metadata": item.metadata_json,
    }


@router.get("/workouts")
def get_workouts(
    limit: int = 1000,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the current user's most recent workouts."""

    limit = max(1, min(limit, 10000))
    records = db.scalars(
        select(Workout)
        .where(Workout.user_id == user.id)
        .order_by(Workout.start_time.desc())
        .limit(limit)
    ).all()
    return [workout_response(item) for item in records]


@router.get("/workouts/{workout_id}")
def get_workout(
    workout_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return one user-owned workout or raise a not-found response."""

    workout = db.scalar(
        select(Workout).where(
            Workout.id == workout_id,
            Workout.user_id == user.id,
        )
    )
    if workout is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    return workout_response(workout)
