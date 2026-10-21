"""Authenticated endpoints for ingesting Apple Health exports."""

import hashlib
import logging
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Body, Depends
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.observability import log_event
from app.database.models import IngestionBatch, User
from app.database.session import get_db
from app.domain.schemas import MetricIn, WorkoutIn
from app.security.auth import get_current_user
from app.services.normalizer import canonical_json, extract_payload
from app.services.persistence import commit_or_rollback, save_metrics, save_workouts

router = APIRouter(prefix="/api/v1/ingest", tags=["ingestion"])
logger = logging.getLogger("apple_health_api")
tracer = trace.get_tracer("apple_health_api.ingestion")


def user_log_context(user: User) -> dict[str, Any]:
    """Return useful identity fields without exposing email or credentials."""
    
    return {
        "user_id": str(user.id),
        "user_name": user.name,
    }


def upload_type(metrics: list[Any], workouts: list[Any]) -> str:
    """Describe which kind of health data an upload contains."""

    if metrics and workouts:
        return "mixed"
    if metrics:
        return "metrics"
    if workouts:
        return "workouts"
    return "empty"


def set_span_context(span: Any, user: User, data_type: str) -> None:
    """Attach searchable request ownership and upload type to a span."""

    span.set_attribute("enduser.id", str(user.id))
    span.set_attribute("enduser.name", user.name)
    span.set_attribute("ingestion.upload_type", data_type)


def summarize_metrics(items: list[Any]) -> dict[str, Any]:
    """Summarize metrics without logging names, sources, or values."""
    
    return {"metric_types": len({item.metric for item in items})}


def workout_detail_count(items: list[Any]) -> int:
    """Count all route, sample, and interval children in workouts."""

    return sum(
        len(getattr(item, field, None) or [])
        for item in items
        for field in ("routes", "samples", "intervals")
    )


def workout_detail_summary(items: list[Any]) -> dict[str, int]:
    """Return privacy-safe counts for normalized workout details."""

    return {
        "route_points": sum(len(item.routes or []) for item in items),
        "workout_samples": sum(len(item.samples or []) for item in items),
        "workout_intervals": sum(len(item.intervals or []) for item in items),
    }


def log_stage(stage: str, started_at: float, **context: Any) -> None:
    """Log the elapsed time and supplied context for an ingestion stage."""

    log_event(
        logger,
        "processing.stage.completed",
        stage=stage,
        duration_ms=round((perf_counter() - started_at) * 1000, 2),
        **context,
    )


@router.post("/metrics")
def ingest_metrics(
    items: list[MetricIn],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Persist a batch of normalized metrics for the authenticated user."""

    request_started = perf_counter()
    context = {**user_log_context(user), "upload_type": "metrics"}
    log_event(
        logger,
        "ingestion.received",
        endpoint="metrics",
        received=len(items),
        **context,
    )
    try:
        with tracer.start_as_current_span("ingestion.persist_metrics") as span:
            set_span_context(span, user, "metrics")
            span.set_attribute("ingestion.items.received", len(items))
            inserted = save_metrics(db, items, user.id)
            commit_or_rollback(db)
            span.set_attribute("ingestion.items.inserted", inserted)
            span.set_attribute("ingestion.items.skipped", len(items) - inserted)
    except Exception as exc:
        db.rollback()
        log_event(
            logger,
            "ingestion.failed",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            endpoint="metrics",
            received=len(items),
            duration_ms=round((perf_counter() - request_started) * 1000, 2),
            **context,
        )
        raise

    log_event(
        logger,
        "ingestion.completed",
        endpoint="metrics",
        received=len(items),
        inserted=inserted,
        skipped=len(items) - inserted,
        duration_ms=round((perf_counter() - request_started) * 1000, 2),
        **context,
    )
    return {
        "received": len(items),
        "inserted": inserted,
        "skipped": len(items) - inserted,
    }


@router.post("/workouts")
def ingest_workouts(
    items: list[WorkoutIn],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Persist a batch of normalized workouts for the authenticated user."""

    request_started = perf_counter()
    details = workout_detail_summary(items)
    context = {**user_log_context(user), "upload_type": "workouts"}
    log_event(
        logger,
        "ingestion.received",
        endpoint="workouts",
        received=len(items),
        **context,
        **details,
    )
    try:
        with tracer.start_as_current_span("ingestion.persist_workouts") as span:
            set_span_context(span, user, "workouts")
            span.set_attribute("ingestion.workouts.received", len(items))
            for key, value in details.items():
                span.set_attribute(f"ingestion.{key}", value)
            inserted = save_workouts(db, items, user.id)
            commit_or_rollback(db)
            span.set_attribute("ingestion.workouts.inserted", inserted)
            span.set_attribute("ingestion.workouts.skipped", len(items) - inserted)
    except Exception as exc:
        db.rollback()
        log_event(
            logger,
            "ingestion.failed",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            endpoint="workouts",
            received=len(items),
            duration_ms=round((perf_counter() - request_started) * 1000, 2),
            **context,
            **details,
        )
        raise

    log_event(
        logger,
        "ingestion.completed",
        endpoint="workouts",
        received=len(items),
        inserted=inserted,
        skipped=len(items) - inserted,
        duration_ms=round((perf_counter() - request_started) * 1000, 2),
        **context,
        **details,
    )
    return {
        "received": len(items),
        "inserted": inserted,
        "skipped": len(items) - inserted,
    }


@router.post("/auto-health-export")
def ingest_auto_health_export(
    payload: Any = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Normalize, deduplicate, and persist an Auto Health Export payload."""

    request_started = perf_counter()
    user_context = user_log_context(user)

    hash_started = perf_counter()
    with tracer.start_as_current_span("ingestion.hash_payload") as span:
        set_span_context(span, user, "unclassified")
        payload_json = canonical_json(payload)
        payload_bytes = len(payload_json.encode("utf-8"))
        digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        span.set_attribute("ingestion.payload.bytes", payload_bytes)
        span.set_attribute("ingestion.payload.type", type(payload).__name__)
    log_stage(
        "payload_hash",
        hash_started,
        payload_bytes=payload_bytes,
        upload_type="unclassified",
        **user_context,
    )
    log_event(
        logger,
        "ingestion.received",
        endpoint="auto_health_export",
        payload_type=type(payload).__name__,
        payload_bytes=payload_bytes,
        upload_type="unclassified",
        **user_context,
    )

    duplicate_started = perf_counter()
    try:
        with tracer.start_as_current_span("ingestion.check_duplicate") as span:
            set_span_context(span, user, "unclassified")
            existing_batch = db.scalar(
                select(IngestionBatch).where(
                    IngestionBatch.user_id == user.id,
                    IngestionBatch.payload_sha256 == digest,
                )
            )
            span.set_attribute("ingestion.duplicate", existing_batch is not None)
    except Exception as exc:
        db.rollback()
        log_event(
            logger,
            "ingestion.duplicate_check.failed",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            endpoint="auto_health_export",
            upload_type="unclassified",
            duration_ms=round((perf_counter() - request_started) * 1000, 2),
            **user_context,
        )
        raise
    log_stage(
        "duplicate_check",
        duplicate_started,
        found=existing_batch is not None,
        upload_type="unclassified",
        **user_context,
    )

    if existing_batch:
        duplicate_metrics, duplicate_workouts = extract_payload(payload)
        duplicate_upload_type = upload_type(duplicate_metrics, duplicate_workouts)
        log_event(
            logger,
            "ingestion.duplicate",
            endpoint="auto_health_export",
            received=existing_batch.item_count,
            inserted=existing_batch.inserted_count,
            skipped=existing_batch.skipped_count,
            duration_ms=round((perf_counter() - request_started) * 1000, 2),
            upload_type=duplicate_upload_type,
            batch_id=str(existing_batch.id),
            **user_context,
        )
        return {
            "status": "duplicate",
            "batch_id": str(existing_batch.id),
            "received": existing_batch.item_count,
            "inserted": existing_batch.inserted_count,
            "skipped": existing_batch.skipped_count,
        }

    normalize_started = perf_counter()
    try:
        with tracer.start_as_current_span("ingestion.normalize") as span:
            metrics, workouts = extract_payload(payload)
            normalized_upload_type = upload_type(metrics, workouts)
            set_span_context(span, user, normalized_upload_type)
            details = workout_detail_summary(workouts)
            span.set_attribute("ingestion.metrics.count", len(metrics))
            span.set_attribute("ingestion.workouts.count", len(workouts))
            span.set_attribute(
                "ingestion.metrics.type_count",
                summarize_metrics(metrics)["metric_types"],
            )
            for key, value in details.items():
                span.set_attribute(f"ingestion.{key}", value)
    except Exception as exc:
        log_event(
            logger,
            "ingestion.normalization.failed",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            payload_bytes=payload_bytes,
            endpoint="auto_health_export",
            upload_type="unclassified",
            duration_ms=round((perf_counter() - normalize_started) * 1000, 2),
            **user_context,
        )
        raise

    workout_details = workout_detail_count(workouts)
    total_items = len(metrics) + len(workouts)
    metric_summary = summarize_metrics(metrics)
    normalized_upload_type = upload_type(metrics, workouts)
    log_event(
        logger,
        "ingestion.normalization.completed",
        metrics=len(metrics),
        metric_types=metric_summary["metric_types"],
        workouts=len(workouts),
        workout_details=workout_details,
        total_items=total_items,
        duration_ms=round((perf_counter() - normalize_started) * 1000, 2),
        endpoint="auto_health_export",
        upload_type=normalized_upload_type,
        **user_context,
        **details,
    )

    db_started = perf_counter()
    try:
        with tracer.start_as_current_span("ingestion.database_transaction") as span:
            set_span_context(span, user, normalized_upload_type)
            with tracer.start_as_current_span("ingestion.write_metrics") as child:
                set_span_context(child, user, "metrics")
                inserted_metrics = save_metrics(db, metrics, user.id)
                child.set_attribute("ingestion.metrics.received", len(metrics))
                child.set_attribute("ingestion.metrics.inserted", inserted_metrics)
            with tracer.start_as_current_span("ingestion.write_workouts") as child:
                set_span_context(child, user, "workouts")
                inserted_workouts = save_workouts(db, workouts, user.id)
                child.set_attribute("ingestion.workouts.received", len(workouts))
                child.set_attribute("ingestion.workouts.inserted", inserted_workouts)
                for key, value in details.items():
                    child.set_attribute(f"ingestion.{key}", value)

            inserted = inserted_metrics + inserted_workouts
            skipped = total_items - inserted
            batch = IngestionBatch(
                user_id=user.id,
                payload_sha256=digest,
                source="auto_health_export",
                item_count=total_items,
                inserted_count=inserted,
                skipped_count=skipped,
                payload=payload,
            )
            with tracer.start_as_current_span("ingestion.commit_batch"):
                db.add(batch)
                db.commit()
                db.refresh(batch)
            span.set_attribute("ingestion.items.received", total_items)
            span.set_attribute("ingestion.items.inserted", inserted)
            span.set_attribute("ingestion.items.skipped", skipped)
    except Exception as exc:
        db.rollback()
        log_event(
            logger,
            "ingestion.database.failed",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            metrics=len(metrics),
            workouts=len(workouts),
            duration_ms=round((perf_counter() - db_started) * 1000, 2),
            endpoint="auto_health_export",
            upload_type=normalized_upload_type,
            **user_context,
            **details,
        )
        raise

    log_stage(
        "database_transaction",
        db_started,
        metrics_inserted=inserted_metrics,
        workouts_inserted=inserted_workouts,
        total_inserted=inserted,
        total_skipped=skipped,
        upload_type=normalized_upload_type,
        **user_context,
    )
    log_event(
        logger,
        "ingestion.completed",
        endpoint="auto_health_export",
        received=total_items,
        inserted=inserted,
        skipped=skipped,
        metrics_inserted=inserted_metrics,
        workouts_inserted=inserted_workouts,
        payload_bytes=payload_bytes,
        duration_ms=round((perf_counter() - request_started) * 1000, 2),
        upload_type=normalized_upload_type,
        batch_id=str(batch.id),
        **user_context,
        **details,
    )
    return {
        "status": "success",
        "batch_id": str(batch.id),
        "received": total_items,
        "inserted": inserted,
        "skipped": skipped,
        "metrics_inserted": inserted_metrics,
        "workouts_inserted": inserted_workouts,
    }
