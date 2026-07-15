"""Tests for /api/v1/auth login + me and role dependencies (Sprint 09)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app.auth import hash_password
from api.app.deps import require_user
from api.app.main import app


@pytest.fixture
def client() -> TestClient:
    store = MagicMock()
    store.get_user.return_value = {
        "username": "dev1",
        "password_hash": hash_password("pw123"),
        "role": "dev",
    }
    app.state.user_store = store
    return TestClient(app)


def test_login_success_returns_token_and_role(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "dev1", "password": "pw123"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "dev"
    assert body["username"] == "dev1"
    assert body["access_token"]


def test_login_wrong_password_401(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "dev1", "password": "nope"}
    )
    assert resp.status_code == 401


def test_login_unknown_user_401(client: TestClient) -> None:
    app.state.user_store.get_user.return_value = None
    resp = client.post(
        "/api/v1/auth/login", json={"username": "ghost", "password": "x"}
    )
    assert resp.status_code == 401


def test_me_roundtrip(client: TestClient) -> None:
    app.dependency_overrides.pop(require_user, None)
    token = client.post(
        "/api/v1/auth/login", json={"username": "dev1", "password": "pw123"}
    ).json()["access_token"]
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "dev1", "role": "dev"}


def test_me_without_token_401(client: TestClient) -> None:
    app.dependency_overrides.pop(require_user, None)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_with_garbage_token_401(client: TestClient) -> None:
    app.dependency_overrides.pop(require_user, None)
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk"})
    assert resp.status_code == 401


def test_business_routes_require_auth() -> None:
    app.dependency_overrides.pop(require_user, None)
    bare = TestClient(app)
    assert bare.get("/forecast-runs/latest").status_code == 401
    assert bare.get("/api/v1/datasets").status_code == 401
    assert bare.post("/retrain/trigger", json={"reason": "x"}).status_code == 401
    # probes stay open (never 401)
    assert bare.get("/version").status_code == 200
    assert bare.get("/metrics").status_code == 200
