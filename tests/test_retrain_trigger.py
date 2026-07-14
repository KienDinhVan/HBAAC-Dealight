"""Tests for /retrain/trigger and /retrain/runs/{id} (Sprint 9)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.app.main import app


@pytest.fixture
def client() -> TestClient:
    fake = AsyncMock()
    fake.trigger_dag = AsyncMock(
        return_value={
            "dag_run_id": "manual__20260527T010000Z",
            "state": "queued",
            "note": "Retrain: drift",
        }
    )
    fake.get_dag_run = AsyncMock(
        return_value={
            "dag_run_id": "manual__20260527T010000Z",
            "state": "success",
            "execution_date": datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat(),
            "start_date": datetime(2026, 5, 27, tzinfo=timezone.utc).isoformat(),
            "end_date": datetime(2026, 5, 27, 0, 10, tzinfo=timezone.utc).isoformat(),
            "note": "Retrain: drift",
        }
    )
    app.state.airflow_client = fake
    return TestClient(app)


def test_retrain_trigger_returns_dag_run_id(client: TestClient) -> None:
    resp = client.post("/retrain/trigger", json={"reason": "drift"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dag_id"] == "train_hbaac_sku"
    assert body["dag_run_id"].startswith("manual__")
    assert body["state"] == "queued"


def test_retrain_run_status(client: TestClient) -> None:
    resp = client.get("/retrain/runs/manual__20260527T010000Z")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "success"
    assert body["dag_run_id"] == "manual__20260527T010000Z"


def test_retrain_trigger_propagates_airflow_failure(client: TestClient) -> None:
    app.state.airflow_client.trigger_dag = AsyncMock(side_effect=RuntimeError("airflow down"))
    resp = client.post("/retrain/trigger", json={"reason": "test"})
    assert resp.status_code == 502
    assert "airflow down" in resp.text
