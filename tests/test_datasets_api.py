from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from api.app.main import app
from hbacc_prj.dataset_config import load_dataset_config
from hbacc_prj import training


class DatasetRepository:
    def summary(
        self, target_date: date, model_names: tuple[str, ...] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        assert model_names == ("sku-demand-lightgbm", "hbaac_sku-forecaster")
        return (
            {
                "forecast_date": date(2026, 7, 14),
                "model_name": "sku-demand-lightgbm",
                "model_version": "7",
            },
            {
                "target_date": target_date,
                "sku_count": 2,
                "total_predicted_quantity": 12.0,
                "avg_predicted_quantity": 6.0,
                "max_predicted_quantity": 8.0,
            },
        )


def test_list_datasets():
    response = TestClient(app).get("/api/v1/datasets")
    assert response.status_code == 200
    datasets = response.json()
    assert [item["name"] for item in datasets] == [
        "hbaac_sku",
        "sample_shop",
    ]
    hbaac = datasets[0]
    assert hbaac["source_type"] == "file"
    assert hbaac["table_name"] == "sales_daily"
    assert hbaac["mapping"]["entity_id"] == "ItemCode"
    assert [dag["dag_id"] for dag in hbaac["dags"]] == [
        "ingest_hbaac_sku",
        "features_hbaac_sku",
        "train_hbaac_sku",
        "forecast_hbaac_sku",
        "monitor_hbaac_sku",
    ]
    assert hbaac["dags"][0]["schedule"] == "0 2 * * *"
    assert hbaac["dags"][2]["schedule"] == "0 4 * * 0"


def test_unknown_dataset_404():
    response = TestClient(app).get(
        "/api/v1/nope/forecast/summary?target_date=2026-01-01"
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown dataset 'nope'"


def test_hbaac_summary_uses_legacy_and_dataset_model_names():
    app.state.repository = DatasetRepository()
    response = TestClient(app).get(
        "/api/v1/hbaac_sku/forecast/summary?target_date=2026-01-01"
    )
    assert response.status_code == 200
    assert response.json()["model_name"] == "sku-demand-lightgbm"
    assert response.json()["sku_count"] == 2


def test_hbaac_training_registers_backward_compatible_alias(monkeypatch):
    captured = {}

    def fake_train_and_log(*args, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(training, "train_and_log", fake_train_and_log)
    config = load_dataset_config("datasets/hbaac_sku.yaml")
    assert training.train_for_dataset(config) == {"ok": True}
    assert captured["registered_model_names"] == (
        "sku-demand-lightgbm",
        "hbaac_sku-forecaster",
    )
    assert captured["min_wape_improvement"] == 0.02
