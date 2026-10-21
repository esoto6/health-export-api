"""Tests for request-scoped database session lifecycle management."""

from unittest.mock import MagicMock

from app.database import session


def test_get_db_yields_and_closes_session(monkeypatch):
    """Verify that the session dependency yields and closes its session."""

    db = MagicMock()
    monkeypatch.setattr(session, "SessionLocal", lambda: db)

    dependency = session.get_db()
    assert next(dependency) is db

    try:
        next(dependency)
    except StopIteration:
        pass

    db.close.assert_called_once_with()
