"""Environment-backed application configuration and validation."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Define configuration values loaded from environment variables."""

    database_url: str = (
        "postgresql+psycopg://health_app:change-me@timescaledb:5432/health"
    )
    log_level: str = "info"

    admin_api_token: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()


def validate_settings():
    """Reject a configured administrator token that is too short."""

    if settings.admin_api_token and len(settings.admin_api_token) < 32:
        raise RuntimeError(
            "ADMIN_API_TOKEN must be at least 32 characters when configured"
        )
