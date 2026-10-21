"""Transactional persistence helpers for metrics and workouts."""

import hashlib
import logging
from time import perf_counter
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.observability import log_event
from app.database.models import (
    HealthMetric,
    Workout,
    WorkoutInterval,
    WorkoutRoute,
    WorkoutSample,
)
from app.services.normalizer import canonical_json

logger = logging.getLogger("apple_health_api")


def model_dict(item: Any) -> dict[str, Any]:
    """Convert a Pydantic model or dictionary into a Python dictionary."""

    if hasattr(item, "model_dump"):
        return cast(dict[str, Any], item.model_dump())
    if isinstance(item, dict):
        return item
    raise TypeError(f"Expected a Pydantic model or dict, got {type(item).__name__}")


def metric_key(item: Any, user_id: UUID) -> str:
    """Generate a stable, user-specific metric sample key."""

    raw = model_dict(item)
    # Resolution is reporting metadata, not part of a HealthKit sample's identity.
    # Excluding it preserves keys generated before the resolution column existed.
    raw.pop("resolution", None)
    sample_id = raw.get("sample_id") or raw.get("id")
    seed = f"{user_id}:{sample_id}" if sample_id else f"{user_id}:{canonical_json(raw)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def save_metrics(db: Session, items: list[Any], user_id: UUID) -> int:
    """Insert metrics in bounded batches without committing."""

    started_at = perf_counter()
    context: dict[str, Any] = {
        "user_id": str(user_id),
        "upload_type": "metrics",
    }
    if not items:
        log_event(
            logger,
            "metrics.write.completed",
            received=0,
            inserted=0,
            skipped=0,
            duration_ms=0.0,
            **context,
        )
        return 0

    batch_size = 500
    inserted = 0
    batch_count = (len(items) + batch_size - 1) // batch_size
    statement = (
        insert(HealthMetric)
        .on_conflict_do_nothing(index_elements=["time", "sample_key"])
        .returning(HealthMetric.time)
    )

    try:
        for start in range(0, len(items), batch_size):
            batch_started = perf_counter()
            batch = [
                {
                    "user_id": user_id,
                    "time": item.start_time,
                    "sample_key": metric_key(item, user_id),
                    "metric": item.metric,
                    "value": item.value,
                    "unit": item.unit,
                    "end_time": getattr(item, "end_time", None),
                    "source": getattr(item, "source", None) or "unknown",
                    "device": getattr(item, "device", None),
                    "resolution": getattr(item, "resolution", "raw"),
                    "metadata_json": (
                        getattr(item, "metadata", None)
                        or getattr(item, "metadata_json", None)
                        or {}
                    ),
                }
                for item in items[start : start + batch_size]
            ]
            result = db.execute(statement, batch)
            batch_inserted = len(result.fetchall())
            inserted += batch_inserted
            batch_duration_ms = (perf_counter() - batch_started) * 1000
            if batch_duration_ms >= 1000:
                log_event(
                    logger,
                    "metrics.write.batch_slow",
                    level=logging.WARNING,
                    batch_number=start // batch_size + 1,
                    batch_count=batch_count,
                    rows=len(batch),
                    inserted=batch_inserted,
                    duration_ms=round(batch_duration_ms, 2),
                    **context,
                )

        log_event(
            logger,
            "metrics.write.completed",
            received=len(items),
            inserted=inserted,
            skipped=len(items) - inserted,
            batch_count=batch_count,
            batch_size=batch_size,
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
            **context,
        )
        return inserted
    except Exception as exc:
        log_event(
            logger,
            "metrics.write.failed",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            rows=len(items),
            batch_count=batch_count,
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
            **context,
        )
        raise


def save_workouts(db: Session, items: list[Any], user_id: UUID) -> int:
    """Upsert workouts and replace their normalized children without committing."""

    started_at = perf_counter()
    context: dict[str, Any] = {
        "user_id": str(user_id),
        "upload_type": "workouts",
    }
    keyed_items = {}
    for item in items:
        raw = model_dict(item)
        source_id = getattr(item, "source_id", None)
        if not source_id:
            source_id = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
        source = getattr(item, "source", None) or "Apple Health"
        keyed_items[(source, str(source_id))] = (
            item,
            {
                "user_id": user_id,
                "source_id": str(source_id),
                "source": source,
                "workout_type": item.workout_type,
                "start_time": item.start_time,
                "end_time": item.end_time,
                "duration_seconds": getattr(item, "duration_seconds", None),
                "total_energy_kcal": getattr(item, "total_energy_kcal", None),
                "active_energy_kcal": getattr(item, "active_energy_kcal", None),
                "average_heart_rate_bpm": getattr(item, "average_heart_rate_bpm", None),
                "max_heart_rate_bpm": getattr(item, "max_heart_rate_bpm", None),
                "min_heart_rate_bpm": getattr(item, "min_heart_rate_bpm", None),
                "effort_score": getattr(item, "effort_score", None),
                "distance_miles": getattr(item, "distance_miles", None),
                "source_name": getattr(item, "source_name", None),
                "metadata_json": (
                    getattr(item, "metadata", None)
                    or getattr(item, "metadata_json", None)
                    or {}
                ),
            },
        )

    if not keyed_items:
        log_event(
            logger,
            "workouts.write.completed",
            received=0,
            inserted=0,
            updated=0,
            duration_ms=0.0,
            **context,
        )
        return 0

    keys = list(keyed_items)
    existing_keys = set(
        db.execute(
            select(Workout.source, Workout.source_id).where(
                Workout.user_id == user_id,
                tuple_(Workout.source, Workout.source_id).in_(keys),
            )
        ).all()
    )
    rows = [row for _, row in keyed_items.values()]
    insert_statement = insert(Workout).values(rows)
    statement = insert_statement.on_conflict_do_update(
        index_elements=["user_id", "source", "source_id"],
        set_={
            "workout_type": insert_statement.excluded.workout_type,
            "start_time": insert_statement.excluded.start_time,
            "end_time": insert_statement.excluded.end_time,
            "distance_miles": insert_statement.excluded.distance_miles,
            "duration_seconds": insert_statement.excluded.duration_seconds,
            "total_energy_kcal": insert_statement.excluded.total_energy_kcal,
            "active_energy_kcal": insert_statement.excluded.active_energy_kcal,
            "average_heart_rate_bpm": insert_statement.excluded.average_heart_rate_bpm,
            "max_heart_rate_bpm": insert_statement.excluded.max_heart_rate_bpm,
            "min_heart_rate_bpm": insert_statement.excluded.min_heart_rate_bpm,
            "effort_score": insert_statement.excluded.effort_score,
            "source_name": insert_statement.excluded.source_name,
            "metadata_json": insert_statement.excluded.metadata_json,
        },
    ).returning(Workout.id, Workout.source, Workout.source_id)
    saved = {(row.source, row.source_id): row.id for row in db.execute(statement)}

    def replace_children(model, workout_id, children, row_builder):
        """Replace one normalized child collection for a saved workout."""

        if children is None:
            return
        db.execute(delete(model).where(model.workout_id == workout_id))
        child_rows = [row_builder(child) for child in children]
        for start in range(0, len(child_rows), 1000):
            db.execute(insert(model), child_rows[start : start + 1000])

    for key, (item, _) in keyed_items.items():
        workout_id = saved[key]
        replace_children(
            WorkoutRoute,
            workout_id,
            getattr(item, "routes", None),
            lambda route, workout_id=workout_id: {
                "workout_id": workout_id,
                "recorded_at": route.recorded_at,
                "sequence": route.sequence,
                "latitude": route.latitude,
                "longitude": route.longitude,
                "altitude_meters": route.altitude_meters,
                "speed": route.speed,
                "course_degrees": route.course_degrees,
                "horizontal_accuracy_meters": route.horizontal_accuracy_meters,
                "vertical_accuracy_meters": route.vertical_accuracy_meters,
                "metadata_json": route.metadata,
            },
        )
        replace_children(
            WorkoutSample,
            workout_id,
            getattr(item, "samples", None),
            lambda sample, workout_id=workout_id: {
                "workout_id": workout_id,
                "time": sample.time,
                "phase": sample.phase,
                "metric": sample.metric,
                "value": sample.value,
                "unit": sample.unit,
                "source": sample.source,
                "metadata_json": sample.metadata,
            },
        )
        replace_children(
            WorkoutInterval,
            workout_id,
            getattr(item, "intervals", None),
            lambda interval, workout_id=workout_id: {
                "workout_id": workout_id,
                "interval_type": interval.interval_type,
                "sequence": interval.sequence,
                "start_time": interval.start_time,
                "end_time": interval.end_time,
                "duration_seconds": interval.duration_seconds,
                "target_distance": interval.target_distance,
                "target_distance_unit": interval.target_distance_unit,
                "metrics_json": interval.metrics,
                "metadata_json": interval.metadata,
            },
        )
    inserted = len(set(keyed_items) - existing_keys)
    log_event(
        logger,
        "workouts.write.completed",
        received=len(items),
        unique=len(keyed_items),
        inserted=inserted,
        updated=len(keyed_items) - inserted,
        route_points=sum(len(getattr(item, "routes", None) or []) for item in items),
        workout_samples=sum(
            len(getattr(item, "samples", None) or []) for item in items
        ),
        workout_intervals=sum(
            len(getattr(item, "intervals", None) or []) for item in items
        ),
        duration_ms=round((perf_counter() - started_at) * 1000, 2),
        **context,
    )
    return inserted


def commit_or_rollback(db: Session) -> None:
    """Commit a transaction, rolling it back if commit fails."""
    
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
