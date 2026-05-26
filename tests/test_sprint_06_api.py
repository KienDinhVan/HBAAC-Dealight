"""Sprint 6 acceptance tests for the FastAPI serving layer.

Covers the full contract surface (200/404/422/503), the OpenAPI document,
and the security smoke checks listed in the sprint plan (no stacktrace
leakage, path-parameter validation, explicit CORS posture).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from api.app.config import Settings
from api.app.main import ITEM_CODE_PATTERN, app


RUN: dict[str, Any] = {
    "run_id": "sprint-05-universe-20250905",
    "forecast_date": date(2025, 9, 5),
    "model_name": "sku-demand-lightgbm",
    "model_version": "5",
    "status": "success",
    "row_count": 894432,
    "started_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
    "finished_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
    "error_message": None,
}


class FakeRepository:
    def ping(self) -> bool:
        return True

    def latest_run(self, forecast_date: date | None = None) -> dict[str, Any] | None:
        return RUN

    def forecast(
        self, item_code: str, days: int, forecast_date: date | None = None
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        if item_code == "UNKNOWN":
            return RUN, []
        anchor = RUN["forecast_date"]
        points = [
            {
                "target_date": anchor + timedelta(days=h),
                "horizon": h,
                "predicted_quantity": 1.5 * h,
            }
            for h in range(1, 57)
        ]
        return RUN, points[:days]

    def top_skus(
        self, target_date: date, limit: int, offset: int = 0
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        items = [
            {
                "item_code": f"SKU-{i:05d}",
                "target_date": target_date,
                "horizon": 1,
                "predicted_quantity": 100.0 - i,
            }
            for i in range(1, 11)
        ]
        return RUN, items[offset : offset + limit]

    def summary(
        self, target_date: date
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        return RUN, {
            "target_date": target_date,
            "sku_count": 15972,
            "total_predicted_quantity": 4321.0,
            "avg_predicted_quantity": 0.27,
            "max_predicted_quantity": 40.0,
        }


class BrokenRepository:
    """Simulates a database outage so we can assert no stacktrace leaks."""

    _LEAK = "psycopg.OperationalError: connection refused at 10.0.0.42"

    def ping(self) -> bool:
        raise RuntimeError(self._LEAK)

    def latest_run(self, forecast_date: date | None = None) -> dict[str, Any] | None:
        raise RuntimeError(self._LEAK)

    def forecast(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(self._LEAK)

    def top_skus(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(self._LEAK)

    def summary(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(self._LEAK)


@pytest.fixture
def client() -> TestClient:
    app.state.repository = FakeRepository()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def broken_client() -> TestClient:
    app.state.repository = BrokenRepository()
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# Contract tests — 200 happy paths
# --------------------------------------------------------------------------- #


def test_health_returns_ok_when_repository_healthy(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "service": "sku-forecast-api",
        "forecast_ready": True,
    }


def test_version_returns_service_metadata(client: TestClient) -> None:
    response = client.get("/version")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "sku-forecast-api"
    assert body["version"]


def test_latest_forecast_run_returns_run_metadata(client: TestClient) -> None:
    response = client.get("/forecast-runs/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == RUN["run_id"]
    assert body["row_count"] == RUN["row_count"]
    assert body["status"] == "success"


def test_current_model_mirrors_latest_run(client: TestClient) -> None:
    latest = client.get("/forecast-runs/latest").json()
    current = client.get("/model/current").json()

    assert current == latest


def test_forecast_by_sku_returns_requested_horizon(client: TestClient) -> None:
    response = client.get("/forecast/SKU-00001?days=3")

    assert response.status_code == 200
    body = response.json()
    assert body["item_code"] == "SKU-00001"
    assert body["forecast_date"] == "2025-09-05"
    assert len(body["forecast"]) == 3
    assert body["forecast"][0]["horizon"] == 1


def test_top_skus_returns_paginated_items(client: TestClient) -> None:
    response = client.get(
        "/forecast/top-skus?target_date=2025-09-15&limit=3&offset=2"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 3
    assert body["offset"] == 2
    assert [item["item_code"] for item in body["items"]] == [
        "SKU-00003",
        "SKU-00004",
        "SKU-00005",
    ]


def test_summary_returns_aggregates(client: TestClient) -> None:
    response = client.get("/forecast/summary?target_date=2025-09-15")

    assert response.status_code == 200
    body = response.json()
    assert body["sku_count"] == 15972
    assert body["max_predicted_quantity"] == 40.0


def test_metrics_endpoint_returns_prometheus_payload(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds" in response.text


# --------------------------------------------------------------------------- #
# Contract tests — 404 / 422 negative paths
# --------------------------------------------------------------------------- #


def test_forecast_unknown_sku_returns_404(client: TestClient) -> None:
    response = client.get("/forecast/UNKNOWN")

    assert response.status_code == 404
    assert "Forecast not found" in response.json()["detail"]


def test_top_skus_missing_target_returns_422(client: TestClient) -> None:
    response = client.get("/forecast/top-skus")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "target_date"]


def test_top_skus_invalid_limit_returns_422(client: TestClient) -> None:
    response = client.get(
        "/forecast/top-skus?target_date=2025-09-15&limit=0"
    )

    assert response.status_code == 422


def test_top_skus_negative_offset_returns_422(client: TestClient) -> None:
    response = client.get(
        "/forecast/top-skus?target_date=2025-09-15&offset=-1"
    )

    assert response.status_code == 422


def test_forecast_invalid_date_returns_422(client: TestClient) -> None:
    response = client.get("/forecast/SKU-00001?forecast_date=not-a-date")

    assert response.status_code == 422


def test_forecast_days_out_of_range_returns_422(client: TestClient) -> None:
    response = client.get("/forecast/SKU-00001?days=99")

    assert response.status_code == 422


@pytest.mark.parametrize(
    "bad_code",
    [
        "SKU 00001",
        "SKU;DROP",
        "SKU'OR'1'='1",
        "A" * 65,
    ],
)
def test_forecast_invalid_item_code_returns_422(
    client: TestClient, bad_code: str
) -> None:
    response = client.get(f"/forecast/{bad_code}")

    assert response.status_code == 422


def test_summary_missing_target_returns_422(client: TestClient) -> None:
    response = client.get("/forecast/summary")

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 503 behaviour — database outage path
# --------------------------------------------------------------------------- #


def test_health_returns_503_when_database_unavailable(
    broken_client: TestClient,
) -> None:
    response = broken_client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


# --------------------------------------------------------------------------- #
# Security smoke
# --------------------------------------------------------------------------- #


def test_database_outage_does_not_leak_stacktrace(
    broken_client: TestClient,
) -> None:
    """Body must not echo internal exception details such as DSN/IP/driver."""

    response = broken_client.get("/health")
    body_text = response.text

    forbidden = [
        "Traceback",
        "psycopg",
        "OperationalError",
        "10.0.0.42",
        "connection refused",
        "RuntimeError",
        "at 0x",
    ]
    for token in forbidden:
        assert token not in body_text, f"Leaked internal detail: {token!r}"


def test_repository_exception_on_protected_route_does_not_leak(
    broken_client: TestClient,
) -> None:
    response = broken_client.get("/forecast-runs/latest")

    # FastAPI returns 500 for unhandled exceptions - that is acceptable so long
    # as no stacktrace/DB internals appear in the body.
    assert response.status_code >= 500
    body_text = response.text
    for token in ("Traceback", "psycopg", "10.0.0.42", "OperationalError"):
        assert token not in body_text, f"Leaked internal detail: {token!r}"


def test_item_code_pattern_is_restrictive() -> None:
    """The route regex must reject control characters, whitespace, and traversal."""

    import re

    pattern = re.compile(ITEM_CODE_PATTERN)
    assert pattern.fullmatch("SKU-00001")
    assert pattern.fullmatch("ABC_123.v2")
    assert not pattern.fullmatch("")
    assert not pattern.fullmatch("../etc/passwd")
    assert not pattern.fullmatch("SKU 00001")
    assert not pattern.fullmatch("SKU\n00001")
    assert not pattern.fullmatch("A" * 65)


def test_settings_cors_origins_default_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When CORS_ORIGINS is unset, the settings expose an empty tuple so the
    middleware is never mounted on the live app."""

    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    from api.app import config

    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.cors_origins == ()
        live_middleware = [type(m.cls).__name__ for m in app.user_middleware]
        assert "CORSMiddleware" not in live_middleware
    finally:
        config.get_settings.cache_clear()


def test_settings_cors_origins_parses_csv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "http://dashboard.example.com, http://ops.example.com",
    )
    from api.app import config

    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.cors_origins == (
            "http://dashboard.example.com",
            "http://ops.example.com",
        )
    finally:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        config.get_settings.cache_clear()


def test_cors_middleware_restricts_origin_when_configured() -> None:
    """When mounted with explicit origins, the middleware must reject others.

    Builds a fresh FastAPI app to avoid touching the live Prometheus registry.
    """

    isolated = FastAPI()
    isolated.add_middleware(
        CORSMiddleware,
        allow_origins=["http://dashboard.example.com"],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @isolated.get("/probe")
    def probe() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(isolated)
    allowed = client.get(
        "/probe", headers={"Origin": "http://dashboard.example.com"}
    )
    denied = client.get(
        "/probe", headers={"Origin": "http://evil.example.com"}
    )

    assert allowed.status_code == 200
    assert (
        allowed.headers.get("access-control-allow-origin")
        == "http://dashboard.example.com"
    )
    assert "access-control-allow-origin" not in denied.headers


def test_default_settings_have_no_wildcard_cors() -> None:
    """Guards against the common mistake of allow_origins=['*']."""

    defaults = Settings()
    assert "*" not in defaults.cors_origins


# --------------------------------------------------------------------------- #
# OpenAPI document
# --------------------------------------------------------------------------- #


def test_openapi_document_is_well_formed(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "SKU Forecast API"
    paths = spec["paths"]
    for path in (
        "/health",
        "/version",
        "/forecast-runs/latest",
        "/model/current",
        "/forecast/top-skus",
        "/forecast/summary",
        "/forecast/{item_code}",
    ):
        assert path in paths, f"Missing OpenAPI path: {path}"


def test_openapi_hides_internal_metrics_endpoint(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()

    assert "/metrics" not in spec["paths"], (
        "Prometheus /metrics must stay out of the public OpenAPI surface"
    )


def test_openapi_does_not_leak_database_url(client: TestClient) -> None:
    """Spec strings must not contain DSN/credential fragments."""

    text = client.get("/openapi.json").text
    for token in ("postgresql://", "psycopg", "DATABASE_URL"):
        assert token not in text, f"Leaked internal token in OpenAPI: {token!r}"
