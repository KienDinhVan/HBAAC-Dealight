"""Tests for /drift/reports list + HTML serving (Sprint 9)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app.main import app


SAMPLE = {
    "report_id": "sprint-07-dag-20260527",
    "run_id": "submission-final-public-048729-20250905",
    "generated_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
    "status": "ok",
    "forecast_row_count": 894432,
    "sku_count": 15972,
    "horizon_count": 56,
    "missing_sku_count": 0,
    "negative_prediction_count": 0,
    "prediction_min": 0.0,
    "prediction_mean": 1.2,
    "prediction_max": 312.5,
    "zero_ratio": 0.34,
    "actual_row_count": 0,
    "accuracy_metrics": {},
    "drift_detected": True,
    "drift_metrics": {"psi": 0.21},
    "alerts": ["psi>0.2"],
    "data_drift_report_path": "data_drift_20260527.html",
    "prediction_drift_report_path": None,
}


class FakeRepo:
    def list_monitoring_reports(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        return [SAMPLE]

    def get_monitoring_report(self, report_id: str) -> dict[str, Any] | None:
        return SAMPLE if report_id == SAMPLE["report_id"] else None

    def latest_monitoring_report(self) -> dict[str, Any] | None:
        return SAMPLE


@pytest.fixture
def client(tmp_path):
    app.state.repository = FakeRepo()
    html = tmp_path / "data_drift_20260527.html"
    html.write_text("<!doctype html><html><body>drift</body></html>")
    from api.app import config as config_module
    from api.app.routers import drift as drift_router

    original = config_module.get_settings()
    patched = replace(original, monitoring_dir=str(tmp_path))
    with patch.object(drift_router, "get_settings", return_value=patched):
        yield TestClient(app)


def test_drift_list_returns_items(client: TestClient) -> None:
    resp = client.get("/drift/reports?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 5
    assert body["items"][0]["report_id"] == SAMPLE["report_id"]
    assert body["items"][0]["drift_detected"] is True


def test_drift_html_serves_file(client: TestClient) -> None:
    resp = client.get(f"/drift/reports/{SAMPLE['report_id']}/html?type=data")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Content-Security-Policy" in resp.headers


def test_drift_html_missing_type_404(client: TestClient) -> None:
    resp = client.get(f"/drift/reports/{SAMPLE['report_id']}/html?type=prediction")
    assert resp.status_code == 404


def test_drift_unknown_report_404(client: TestClient) -> None:
    resp = client.get("/drift/reports/does-not-exist/html?type=data")
    assert resp.status_code == 404
