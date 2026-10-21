"""SQLAlchemy engine, declarative base, and request-scoped sessions."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url, pool_pre_ping=True, pool_size=3, max_overflow=2
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Serve as the declarative base for all database models."""


def get_db():
    """Yield a database session and close it after request processing."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
