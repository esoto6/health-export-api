"""SQLAlchemy models for users, ingestion records, and health data."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.database.session import Base


class User(Base):
    """Represent a person who owns isolated health data and API keys."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)


class ApiKey(Base):
    """Store a hashed, revocable bearer key for a user."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class IngestionBatch(Base):
    """Record an uploaded payload and its deduplication metadata."""

    __tablename__ = "ingestion_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    received_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(
        String(100), nullable=False, default="auto_health_export"
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "payload_sha256", name="uq_ingestion_batch_user_payload"
        ),
        Index("ix_ingestion_batches_user_received", "user_id", "received_at"),
    )


class HealthMetric(Base):
    """Store one normalized time-series health observation."""

    __tablename__ = "health_metrics"

    time: Mapped[object] = mapped_column(DateTime(timezone=True), primary_key=True)
    sample_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    end_time: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="unknown")
    device: Mapped[str | None] = mapped_column(String(200))
    resolution: Mapped[str] = mapped_column(String(20), nullable=False, default="raw")
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_health_metrics_user_metric_time", "user_id", "metric", "time"),
        CheckConstraint(
            "resolution IN ('raw', 'daily', 'weekly', 'monthly')",
            name="ck_health_metrics_resolution",
        ),
    )


class Workout(Base):
    """Store a normalized workout and its summary measurements."""

    __tablename__ = "workouts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Apple Health"
    )
    workout_type: Mapped[str] = mapped_column(String(100), nullable=False)
    start_time: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    total_energy_kcal: Mapped[float | None] = mapped_column(Float)
    active_energy_kcal: Mapped[float | None] = mapped_column(Float)
    distance_miles: Mapped[float | None] = mapped_column(Float)
    average_heart_rate_bpm: Mapped[float | None] = mapped_column(Float)
    max_heart_rate_bpm: Mapped[float | None] = mapped_column(Float)
    min_heart_rate_bpm: Mapped[float | None] = mapped_column(Float)
    effort_score: Mapped[float | None] = mapped_column(Float)
    source_name: Mapped[str | None] = mapped_column(String(200))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "source_id",
            name="uq_workout_user_source_source_id",
        ),
        CheckConstraint("end_time >= start_time", name="ck_workouts_time"),
        Index("ix_workouts_user_start_time", "user_id", "start_time"),
        Index(
            "ix_workouts_user_type_start",
            "user_id",
            "workout_type",
            "start_time",
        ),
    )


class WorkoutSample(Base):
    """Store a time-series sample captured during or after a workout."""

    __tablename__ = "workout_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    time: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False, default="workout")
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str | None] = mapped_column(String(200))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_workout_samples_workout_time", "workout_id", "time"),
        Index(
            "ix_workout_samples_workout_metric_time",
            "workout_id",
            "metric",
            "time",
        ),
        CheckConstraint(
            "phase IN ('workout', 'recovery')", name="ck_workout_sample_phase"
        ),
    )


class WorkoutRoute(Base):
    """Store one ordered geographic point in a workout route."""

    __tablename__ = "workout_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    recorded_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude_meters: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    course_degrees: Mapped[float | None] = mapped_column(Float)
    horizontal_accuracy_meters: Mapped[float | None] = mapped_column(Float)
    vertical_accuracy_meters: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("workout_id", "sequence", name="uq_workout_route_sequence"),
        Index("ix_workout_routes_workout_time", "workout_id", "recorded_at"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_route_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_route_longitude"),
    )


class WorkoutInterval(Base):
    """Store a workout segment or distance split."""

    __tablename__ = "workout_intervals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    interval_type: Mapped[str] = mapped_column(String(20), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    target_distance: Mapped[float | None] = mapped_column(Float)
    target_distance_unit: Mapped[str | None] = mapped_column(String(20))
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "workout_id",
            "interval_type",
            "sequence",
            name="uq_workout_interval_sequence",
        ),
        Index("ix_workout_intervals_workout_type", "workout_id", "interval_type"),
        CheckConstraint(
            "interval_type IN ('segment', 'split')",
            name="ck_workout_interval_type",
        ),
    )
