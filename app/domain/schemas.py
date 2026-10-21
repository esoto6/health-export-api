"""Validated domain schemas used by normalization and persistence."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MetricIn(BaseModel):
    """Represent one normalized health metric observation."""

    model_config = ConfigDict(extra="allow")

    metric: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=40)
    start_time: datetime
    end_time: datetime | None = None
    source: str = "unknown"
    device: str | None = None
    sample_id: str | None = None
    resolution: str = Field(default="raw", pattern="^(raw|daily|weekly|monthly)$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkoutRouteIn(BaseModel):
    """Represent one validated point in an ordered workout route."""

    sequence: int = Field(ge=0)
    recorded_at: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_meters: float | None = None
    speed: float | None = None
    course_degrees: float | None = None
    horizontal_accuracy_meters: float | None = None
    vertical_accuracy_meters: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkoutSampleIn(BaseModel):
    """Represent a metric sample recorded during workout or recovery."""

    time: datetime
    phase: str = Field(default="workout", pattern="^(workout|recovery)$")
    metric: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=40)
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkoutIntervalIn(BaseModel):
    """Represent a normalized workout segment or distance split."""

    interval_type: str = Field(pattern="^(segment|split)$")
    sequence: int = Field(ge=0)
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float | None = None
    target_distance: float | None = None
    target_distance_unit: str | None = Field(default=None, max_length=20)
    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkoutIn(BaseModel):
    """Represent a normalized workout and its optional child records."""

    model_config = ConfigDict(extra="allow")

    source_id: str = Field(min_length=1, max_length=200)
    source: str = "Apple Health"
    workout_type: str = Field(min_length=1, max_length=100)
    start_time: datetime
    end_time: datetime
    duration_seconds: float | None = None
    total_energy_kcal: float | None = None
    active_energy_kcal: float | None = None
    distance_miles: float | None = None
    average_heart_rate_bpm: float | None = None
    max_heart_rate_bpm: float | None = None
    min_heart_rate_bpm: float | None = None
    effort_score: float | None = None
    source_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    routes: list[WorkoutRouteIn] | None = None
    samples: list[WorkoutSampleIn] | None = None
    intervals: list[WorkoutIntervalIn] | None = None


class MetricBatch(BaseModel):
    """Wrap a list of normalized health metrics for batch ingestion."""

    data: list[MetricIn]


class WorkoutBatch(BaseModel):
    """Wrap a list of normalized workouts for batch ingestion."""

    data: list[WorkoutIn]
