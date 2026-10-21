"""Tests for application construction, routing, and lifespan behavior."""

import asyncio
from unittest.mock import MagicMock

from app import main
from app.api.routes.system import health
from app.main import app


def test_health_endpoint():
    """Verify that the health endpoint reports availability."""

    assert health() == {"status": "ok"}


def test_expected_api_routes_are_registered():
    """Verify that the application registers every public API route."""

    paths = set(app.openapi()["paths"])

    assert paths == {
        "/api/v1/admin/api-keys",
        "/api/v1/ingest/auto-health-export",
        "/api/v1/ingest/metrics",
        "/api/v1/ingest/workouts",
        "/api/v1/metrics",
        "/api/v1/workouts",
        "/api/v1/workouts/{workout_id}",
        "/health",
    }


def test_create_app_registers_routes_and_observability(monkeypatch):
    """Verify that application creation configures routes and telemetry."""

    configure = MagicMock()
    monkeypatch.setattr(main, "configure_observability", configure)

    application = main.create_app()

    assert application.title == "Apple Health API"
    assert "/api/v1/ingest/metrics" in application.openapi()["paths"]
    configure.assert_called_once_with(application)


def test_lifespan_validates_settings(monkeypatch):
    """Verify that startup validates runtime configuration."""

    validate = MagicMock()
    monkeypatch.setattr(main, "validate_settings", validate)

    async def run_lifespan():
        """Enter and exit the application lifespan context."""

        async with main.lifespan(app):
            validate.assert_called_once_with()

    asyncio.run(run_lifespan())
