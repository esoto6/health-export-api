"""Tests for environment validation and bearer-key authentication."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import dependencies
from app.core import config
from app.database.models import ApiKey
from app.security.auth import get_current_user


def credentials(token: str) -> HTTPAuthorizationCredentials:
    """Build HTTP bearer credentials containing a test token."""

    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_validate_settings_accepts_empty_or_long_admin_token(monkeypatch):
    """Verify that empty and sufficiently long administrator tokens pass."""

    monkeypatch.setattr(config.settings, "admin_api_token", "")
    assert config.validate_settings() is None

    monkeypatch.setattr(config.settings, "admin_api_token", "x" * 32)
    assert config.validate_settings() is None


def test_validate_settings_rejects_short_admin_token(monkeypatch):
    """Verify that short administrator tokens are rejected."""

    monkeypatch.setattr(config.settings, "admin_api_token", "too-short")

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        config.validate_settings()


def test_require_admin_token_handles_configuration_and_credentials(monkeypatch):
    """Verify administrator authentication failures and success."""

    monkeypatch.setattr(dependencies.settings, "admin_api_token", "")
    with pytest.raises(HTTPException) as exc:
        dependencies.require_admin_token(credentials("anything"))
    assert exc.value.status_code == 503

    expected = "a" * 32
    monkeypatch.setattr(dependencies.settings, "admin_api_token", expected)
    with pytest.raises(HTTPException) as exc:
        dependencies.require_admin_token(credentials("wrong"))
    assert exc.value.status_code == 403

    assert dependencies.require_admin_token(credentials(expected)) is None


def test_get_current_user_hashes_key_and_returns_owner(test_user):
    """Verify user authentication hashes the key and returns its owner."""

    db = MagicMock()
    db.scalar.return_value = ApiKey(user_id=test_user.id, name="phone", key_hash="x")
    db.get.return_value = test_user

    result = get_current_user(credentials("fixture-key"), db)

    assert result is test_user
    db.scalar.assert_called_once()
    db.get.assert_called_once_with(type(test_user), test_user.id)


def test_get_current_user_rejects_unknown_key():
    """Verify that an unknown user key is rejected."""

    db = MagicMock()
    db.scalar.return_value = None

    with pytest.raises(HTTPException) as exc:
        get_current_user(credentials("unknown"), db)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid API key"


def test_get_current_user_rejects_orphaned_key(test_user):
    """Verify that a key without an owning user is rejected."""

    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(user_id=test_user.id)
    db.get.return_value = None

    with pytest.raises(HTTPException) as exc:
        get_current_user(credentials("orphaned"), db)

    assert exc.value.status_code == 401
    assert exc.value.detail == "API key user not found"
