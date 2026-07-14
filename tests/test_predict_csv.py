"""Tests for /predict/csv inline and async branches (Sprint 9)."""
from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.app.main import app
from api.app.routers import predict as predict_router


@pytest.fixture(autouse=True)
def _reset_jobs():
    predict_router._JOBS.clear()
    yield
    predict_router._JOBS.clear()


@pytest.fixture
def client() -> TestClient:
    app.state.airflow_client = AsyncMock()
    app.state.airflow_client.trigger_dag = AsyncMock(
        return_value={"dag_run_id": "manual__test", "state": "queued"}
    )
    app.state.airflow_client.get_dag_run = AsyncMock(
        return_value={"dag_run_id": "manual__test", "state": "success"}
    )
    return TestClient(app)


def _csv_bytes(rows: int = 10) -> bytes:
    lines = ["Date,ItemCode,Quantity,SalesAmount"]
    for i in range(rows):
        lines.append(f"2025-09-0{(i % 9) + 1},SKU-{i:05d},{i + 1},{(i + 1) * 1000}")
    return ("\n".join(lines) + "\n").encode()


def test_predict_csv_inline_returns_items(client: TestClient) -> None:
    files = {"file": ("sample.csv", io.BytesIO(_csv_bytes(20)), "text/csv")}
    resp = client.post("/predict/csv", files=files)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    assert body["mode"] == "inline"
    assert body["status"] == "completed"
    assert body["rows"] == 20
    assert len(body["items"]) > 0
    assert "predicted_quantity" in body["items"][0]


def test_predict_csv_missing_required_column_returns_400(client: TestClient) -> None:
    bad = b"Date,Foo\n2025-09-01,1\n"
    resp = client.post("/predict/csv", files={"file": ("bad.csv", io.BytesIO(bad), "text/csv")})
    assert resp.status_code == 400
    assert "ItemCode" in resp.text


def test_predict_csv_async_path_triggers_airflow(client: TestClient, tmp_path, monkeypatch) -> None:
    from api.app import config as config_module
    from dataclasses import replace

    original = config_module.get_settings()
    patched = replace(
        original, inline_predict_max_rows=5, upload_dir=str(tmp_path)
    )
    monkeypatch.setattr(predict_router, "get_settings", lambda: patched)

    files = {"file": ("big.csv", io.BytesIO(_csv_bytes(20)), "text/csv")}
    resp = client.post("/predict/csv", files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "async"
    assert body["status"] == "queued"
    assert body["dag_run_id"] == "manual__test"
    assert app.state.airflow_client.trigger_dag.await_args.args[0] == (
        "forecast_hbaac_sku"
    )
    saved = list(tmp_path.glob("*.csv"))
    assert len(saved) == 1

    job_resp = client.get(f"/predict/jobs/{body['job_id']}")
    assert job_resp.status_code == 200
    assert job_resp.json()["status"] == "completed"
    assert app.state.airflow_client.get_dag_run.await_args.args[0] == (
        "forecast_hbaac_sku"
    )
