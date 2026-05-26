from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from api.app.main import app


class FakeRepository:
    run = {
        "run_id": "submission-final-public-048729-20250905",
        "forecast_date": date(2025, 9, 5),
        "model_name": "twostage-seed-ensemble-mapoldnew",
        "model_version": "public-0.48729",
        "status": "success",
        "row_count": 894432,
        "started_at": datetime(2026, 5, 26, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 5, 26, tzinfo=timezone.utc),
        "error_message": None,
    }

    def ping(self) -> bool:
        return True

    def latest_run(self, forecast_date=None):
        return self.run

    def forecast(self, item_code, days, forecast_date=None):
        if item_code == "UNKNOWN":
            return self.run, []
        return self.run, [
            {
                "target_date": date(2025, 9, 6),
                "horizon": 1,
                "predicted_quantity": 1.25,
            }
        ][:days]

    def top_skus(self, target_date, limit):
        return self.run, [
            {
                "item_code": "SKU-00001",
                "target_date": target_date,
                "horizon": 1,
                "predicted_quantity": 2.5,
            }
        ][:limit]

    def summary(self, target_date):
        return self.run, {
            "target_date": target_date,
            "sku_count": 15972,
            "total_predicted_quantity": 100.0,
            "avg_predicted_quantity": 1.0,
            "max_predicted_quantity": 10.0,
        }


class EmptyForecastRepository(FakeRepository):
    def latest_run(self, forecast_date=None):
        return None


def _client() -> TestClient:
    app.state.repository = FakeRepository()
    return TestClient(app)


def test_health_and_latest_run() -> None:
    client = _client()

    assert client.get("/health").json()["status"] == "ok"
    latest = client.get("/forecast-runs/latest")
    assert latest.status_code == 200
    assert latest.json()["row_count"] == 894432


def test_health_does_not_require_a_forecast_run_in_foundation_sprint() -> None:
    app.state.repository = EmptyForecastRepository()
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["forecast_ready"] is False


def test_forecast_contract_and_missing_sku() -> None:
    client = _client()

    response = client.get("/forecast/SKU-00001?days=1")
    assert response.status_code == 200
    assert response.json()["forecast"][0]["predicted_quantity"] == 1.25
    assert client.get("/forecast/UNKNOWN").status_code == 404


def test_static_forecast_routes_are_not_parsed_as_sku() -> None:
    client = _client()

    top = client.get("/forecast/top-skus?target_date=2025-09-06&limit=1")
    summary = client.get("/forecast/summary?target_date=2025-09-06")
    assert top.status_code == 200
    assert top.json()["items"][0]["item_code"] == "SKU-00001"
    assert summary.status_code == 200
    assert summary.json()["sku_count"] == 15972


def test_metrics_endpoint() -> None:
    response = _client().get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.text
