"""Tests for model versions/compare + promotion request endpoints (Sprint 09)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app.auth import AuthUser
from api.app.deps import require_user
from api.app.main import app

VERSIONS = [
    {"version": "2", "run_id": "r2", "created_at": 2000, "aliases": ["staging"],
     "metrics": {"lightgbm_wape": 0.4}},
    {"version": "1", "run_id": "r1", "created_at": 1000, "aliases": ["production"],
     "metrics": {"lightgbm_wape": 0.5}},
]


def _request_row(**overrides):
    row = {
        "id": 1, "dataset": "hbaac_sku", "model_name": "hbaac_sku-forecaster",
        "candidate_version": "2", "current_prod_version": "1",
        "metrics_snapshot": {"candidate": VERSIONS[0], "production": VERSIONS[1]},
        "requested_by": "test-dev", "request_note": None, "status": "pending",
        "reviewed_by": None, "review_comment": None,
        "created_at": "2026-07-15T00:00:00Z", "reviewed_at": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def registry() -> MagicMock:
    reg = MagicMock()
    reg.list_versions.return_value = VERSIONS
    reg.compare.return_value = {"candidate": VERSIONS[0], "production": VERSIONS[1]}
    reg.get_alias_version.side_effect = lambda name, alias: {
        "production": "1", "staging": "2"
    }.get(alias)
    reg.promote.return_value = "1"
    return reg


@pytest.fixture
def store() -> MagicMock:
    st = MagicMock()
    st.has_pending.return_value = False
    st.create_request.return_value = _request_row()
    st.get.return_value = _request_row()
    st.list_requests.return_value = [_request_row()]
    st.mark_reviewed.return_value = _request_row(status="approved", reviewed_by="m1")
    return st


@pytest.fixture
def client(registry: MagicMock, store: MagicMock) -> TestClient:
    app.state.model_registry = registry
    app.state.promotion_store = store
    app.state.model_cache = MagicMock()
    return TestClient(app)


def _as_manager():
    app.dependency_overrides[require_user] = lambda: AuthUser(
        username="m1", role="manager"
    )


def test_list_versions(client: TestClient) -> None:
    resp = client.get("/api/v1/models/hbaac_sku/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "hbaac_sku-forecaster"
    assert [v["version"] for v in body["versions"]] == ["2", "1"]


def test_compare_defaults_to_staging(client: TestClient, registry: MagicMock) -> None:
    resp = client.get("/api/v1/models/hbaac_sku/compare")
    assert resp.status_code == 200
    registry.compare.assert_called_with("hbaac_sku-forecaster", "2")


def test_compare_no_staging_404(client: TestClient, registry: MagicMock) -> None:
    registry.get_alias_version.side_effect = lambda name, alias: None
    assert client.get("/api/v1/models/hbaac_sku/compare").status_code == 404


def test_create_request(client: TestClient, store: MagicMock) -> None:
    resp = client.post(
        "/api/v1/models/hbaac_sku/promotion-requests",
        json={"candidate_version": "2", "note": "better wape"},
    )
    assert resp.status_code == 200, resp.text
    assert store.create_request.call_args.kwargs["requested_by"] == "test-dev"


def test_create_request_duplicate_409(client: TestClient, store: MagicMock) -> None:
    store.has_pending.return_value = True
    resp = client.post(
        "/api/v1/models/hbaac_sku/promotion-requests",
        json={"candidate_version": "2"},
    )
    assert resp.status_code == 409


def test_create_request_candidate_is_production_409(
    client: TestClient, registry: MagicMock
) -> None:
    resp = client.post(
        "/api/v1/models/hbaac_sku/promotion-requests",
        json={"candidate_version": "1"},
    )
    assert resp.status_code == 409


def test_approve_requires_manager(client: TestClient) -> None:
    resp = client.post("/api/v1/promotion-requests/1/approve", json={})
    assert resp.status_code == 403


def test_approve_flips_alias_and_invalidates_cache(
    client: TestClient, registry: MagicMock, store: MagicMock
) -> None:
    _as_manager()
    resp = client.post("/api/v1/promotion-requests/1/approve", json={})
    assert resp.status_code == 200, resp.text
    registry.promote.assert_called_once_with("hbaac_sku-forecaster", "2")
    app.state.model_cache.invalidate.assert_called_once_with("hbaac_sku")
    assert store.mark_reviewed.call_args.args[1] == "approved"


def test_approve_missing_candidate_409(
    client: TestClient, registry: MagicMock
) -> None:
    _as_manager()
    registry.list_versions.return_value = [VERSIONS[1]]  # only v1 exists
    assert client.post("/api/v1/promotion-requests/1/approve", json={}).status_code == 409


def test_approve_already_reviewed_409(client: TestClient, store: MagicMock) -> None:
    _as_manager()
    store.get.return_value = _request_row(status="approved")
    assert client.post("/api/v1/promotion-requests/1/approve", json={}).status_code == 409


def test_reject_records_comment(client: TestClient, store: MagicMock) -> None:
    _as_manager()
    store.mark_reviewed.return_value = _request_row(status="rejected")
    resp = client.post(
        "/api/v1/promotion-requests/1/reject", json={"comment": "not enough gain"}
    )
    assert resp.status_code == 200
    assert store.mark_reviewed.call_args.args[1] == "rejected"


def test_list_requests_filter(client: TestClient, store: MagicMock) -> None:
    resp = client.get("/api/v1/promotion-requests?status=pending")
    assert resp.status_code == 200
    store.list_requests.assert_called_with(status="pending")
