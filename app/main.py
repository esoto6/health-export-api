"""FastAPI application factory and default ASGI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import admin, health_data, ingestion, system
from app.core.config import validate_settings
from app.core.observability import configure_observability


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Validate runtime configuration before accepting requests."""

    validate_settings()
    yield


def create_app() -> FastAPI:
    """Build the application and register its route modules."""
    
    application = FastAPI(
        title="Apple Health API",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.include_router(system.router)
    application.include_router(admin.router)
    application.include_router(ingestion.router)
    application.include_router(health_data.router)
    configure_observability(application)
    return application


app = create_app()
