"""Shared pytest fixtures for API, user, and payload tests."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from app.database.models import User

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER_ID = UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def load_fixture():
    """Load an isolated JSON fixture by filename."""

    def _load(filename: str):
        """Load and decode one synthetic JSON fixture."""

        with (FIXTURES / filename).open(encoding="utf-8") as fixture:
            return json.load(fixture)

    return _load


@pytest.fixture
def test_user():
    """Provide a deterministic user record for authenticated tests."""

    return User(
        id=TEST_USER_ID,
        name="Fixture User",
        email="fixture@example.test",
    )
