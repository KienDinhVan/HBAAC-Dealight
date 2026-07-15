"""Shared fixtures: bypass auth for pre-Sprint-09 API tests."""
from __future__ import annotations

import pytest

from api.app.auth import AuthUser
from api.app.deps import require_user
from api.app.main import app


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[require_user] = lambda: AuthUser(
        username="test-dev", role="dev"
    )
    yield
    app.dependency_overrides.pop(require_user, None)
