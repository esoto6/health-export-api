"""Structured logging and OpenTelemetry configuration."""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter,
)
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class JsonConsoleFormatter(logging.Formatter):
    """Render privacy-safe application records with trace correlation."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record with trace correlation as compact JSON."""

        span_context = trace.get_current_span().get_span_context()
        trace_id = (
            format(span_context.trace_id, "032x") if span_context.is_valid else None
        )
        span_id = (
            format(span_context.span_id, "016x") if span_context.is_valid else None
        )
        output: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": getattr(record, "event_name", record.getMessage()),
            "trace_id": trace_id,
            "span_id": span_id,
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            output.update(fields)
        if record.exc_info:
            exception_type = record.exc_info[0]
            if exception_type is not None:
                output["error_type"] = exception_type.__name__
        return json.dumps(output, separators=(",", ":"), default=str)


class JsonConsoleHandler(logging.StreamHandler):
    """Marker handler used to prevent duplicate console configuration."""


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a structured event without serializing payload contents."""
    
    logger.log(
        level,
        event,
        extra={"event_name": event, "event_fields": fields},
    )


def configure_observability(app) -> None:
    """Configure logging, tracing, metrics, and FastAPI instrumentation."""

    service_name = os.getenv("OTEL_SERVICE_NAME", "apple-health-api")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip().rstrip("/")
    deployment_environment = os.getenv("DEPLOYMENT_ENVIRONMENT", "development")

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.getenv("APP_VERSION", "1.0.0"),
            "deployment.environment.name": deployment_environment,
        }
    )

    # Traces
    tracer_provider = TracerProvider(resource=resource)
    if endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"{endpoint}/v1/traces",
                )
            )
        )
    trace.set_tracer_provider(tracer_provider)

    # Metrics
    metric_readers = []
    if endpoint:
        metric_readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=f"{endpoint}/v1/metrics",
                ),
                export_interval_millis=15000,
            )
        )
    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
    metrics.set_meter_provider(meter_provider)

    # Logs
    logger_provider = LoggerProvider(resource=resource)
    if endpoint:
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(
                    endpoint=f"{endpoint}/v1/logs",
                )
            )
        )
    set_logger_provider(logger_provider)

    # Forward standard Python logging records to OpenTelemetry.
    root_logger = logging.getLogger()
    root_logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    if not any(
        isinstance(handler, JsonConsoleHandler) for handler in root_logger.handlers
    ):
        console_handler = JsonConsoleHandler(sys.stdout)
        console_handler.setLevel(root_logger.level)
        console_handler.setFormatter(JsonConsoleFormatter())
        root_logger.addHandler(console_handler)

    # Avoid adding duplicate handlers if configuration runs more than once.
    if endpoint and not any(
        isinstance(handler, LoggingHandler) for handler in root_logger.handlers
    ):
        root_logger.addHandler(
            LoggingHandler(
                level=logging.NOTSET,
                logger_provider=logger_provider,
            )
        )

    # Include trace and span IDs in standard Python log records.
    LoggingInstrumentor().instrument(inject_trace_context=True)

    # Automatically create HTTP request spans and standard FastAPI metrics.
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        # Match the complete health-check URL, not "health" in hosts or routes.
        excluded_urls=r"^https?://[^/]+/health(?:\?.*)?$",
    )

    log_event(
        logging.getLogger("apple_health_api.observability"),
        "observability.configured",
        service_name=service_name,
        deployment_environment=deployment_environment,
        otlp_export_enabled=bool(endpoint),
    )
