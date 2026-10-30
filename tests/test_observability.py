"""Tests for structured logging and OpenTelemetry configuration."""

import json
import logging
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from opentelemetry.util.http import parse_excluded_urls

from app.core import observability
from app.core.observability import JsonConsoleFormatter, log_event


def test_json_console_formatter_uses_structured_fields():
    """Verify JSON logs include structured event and trace fields."""

    record = logging.LogRecord(
        name="apple_health_api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ingestion.completed",
        args=(),
        exc_info=None,
    )
    record.event_name = "ingestion.completed"
    record.event_fields = {
        "received": 10,
        "inserted": 9,
        "skipped": 1,
    }

    output = json.loads(JsonConsoleFormatter().format(record))

    assert output["event"] == "ingestion.completed"
    assert output["received"] == 10
    assert output["inserted"] == 9
    assert output["skipped"] == 1
    assert "trace_id" in output
    assert "span_id" in output


def test_json_console_formatter_records_exception_type():
    """Verify exception logs expose type without the private message."""

    try:
        raise ValueError("private detail")
    except ValueError:
        record = logging.LogRecord(
            name="apple_health_api",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request.failed",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )

    output = json.loads(JsonConsoleFormatter().format(record))
    assert output["error_type"] == "ValueError"
    assert "private detail" not in output


def test_log_event_attaches_structured_fields():
    """Verify structured event fields are attached to log records."""

    logger = MagicMock()

    log_event(logger, "fixture.event", level=logging.WARNING, count=3)

    logger.log.assert_called_once_with(
        logging.WARNING,
        "fixture.event",
        extra={"event_name": "fixture.event", "event_fields": {"count": 3}},
    )


@pytest.mark.parametrize(
    ("url", "excluded"),
    [
        ("http://127.0.0.1:8000/health", True),
        ("https://health.rubberduckops.com/health", True),
        ("https://health.rubberduckops.com/health?ready=1", True),
        ("http://192.168.1.82:8000/api/v1/ingest/auto-health-export", False),
        ("https://health.rubberduckops.com/api/v1/ingest/auto-health-export", False),
        ("https://health.rubberduckops.com/api/v1/metrics", False),
        ("http://127.0.0.1:8000/healthcheck", False),
        ("http://127.0.0.1:8000/health/details", False),
    ],
)
def test_configure_observability_without_exporter(monkeypatch, url, excluded):
    """Exclude only the health endpoint, never ingestion or health hostnames."""

    tracer_provider = MagicMock()
    meter_provider = MagicMock()
    logger_provider = MagicMock()
    instrument_fastapi = MagicMock()
    instrument_logging = MagicMock()

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(observability, "TracerProvider", lambda **_: tracer_provider)
    monkeypatch.setattr(observability, "MeterProvider", lambda **_: meter_provider)
    monkeypatch.setattr(observability, "LoggerProvider", lambda **_: logger_provider)
    monkeypatch.setattr(observability.trace, "set_tracer_provider", MagicMock())
    monkeypatch.setattr(observability.metrics, "set_meter_provider", MagicMock())
    monkeypatch.setattr(observability, "set_logger_provider", MagicMock())
    monkeypatch.setattr(
        observability.FastAPIInstrumentor, "instrument_app", instrument_fastapi
    )
    monkeypatch.setattr(
        observability.LoggingInstrumentor,
        "instrument",
        instrument_logging,
    )

    app = FastAPI()
    observability.configure_observability(app)

    tracer_provider.add_span_processor.assert_not_called()
    assert meter_provider is not None
    logger_provider.add_log_record_processor.assert_not_called()
    instrument_fastapi.assert_called_once()
    instrument_logging.assert_called_once_with(inject_trace_context=True)
    exclusions = parse_excluded_urls(
        instrument_fastapi.call_args.kwargs["excluded_urls"]
    )
    assert exclusions.url_disabled(url) is excluded


def test_configure_observability_builds_otlp_endpoints(monkeypatch):
    """Verify telemetry exporters receive the correct OTLP endpoints."""

    trace_exporter = MagicMock()
    metric_exporter = MagicMock()
    log_exporter = MagicMock()
    tracer_provider = MagicMock()
    logger_provider = MagicMock()

    class FakeLoggingHandler(logging.NullHandler):
        """Capture the logger provider supplied during configuration."""

        def __init__(self, *, level, logger_provider):
            """Initialize the fake handler with its logger provider."""

            super().__init__(level)
            self.logger_provider = logger_provider

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318/")
    monkeypatch.setattr(observability, "OTLPSpanExporter", trace_exporter)
    monkeypatch.setattr(observability, "OTLPMetricExporter", metric_exporter)
    monkeypatch.setattr(observability, "OTLPLogExporter", log_exporter)
    monkeypatch.setattr(observability, "TracerProvider", lambda **_: tracer_provider)
    monkeypatch.setattr(observability, "LoggerProvider", lambda **_: logger_provider)
    monkeypatch.setattr(observability, "LoggingHandler", FakeLoggingHandler)
    monkeypatch.setattr(observability.trace, "set_tracer_provider", MagicMock())
    monkeypatch.setattr(observability.metrics, "set_meter_provider", MagicMock())
    monkeypatch.setattr(observability, "set_logger_provider", MagicMock())
    monkeypatch.setattr(
        observability.FastAPIInstrumentor, "instrument_app", MagicMock()
    )
    monkeypatch.setattr(observability.LoggingInstrumentor, "instrument", MagicMock())

    observability.configure_observability(FastAPI())

    trace_exporter.assert_called_once_with(endpoint="http://collector:4318/v1/traces")
    metric_exporter.assert_called_once_with(endpoint="http://collector:4318/v1/metrics")
    log_exporter.assert_called_once_with(endpoint="http://collector:4318/v1/logs")
