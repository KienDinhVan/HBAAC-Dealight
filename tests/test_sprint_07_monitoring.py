from __future__ import annotations

import pandas as pd

from hbacc_prj.monitoring import (
    ForecastQuality,
    drift_summary,
    evaluate_alerts,
    regression_metrics,
    summarize_forecast,
)


def test_forecast_quality_accepts_complete_non_negative_forecast() -> None:
    forecast = pd.DataFrame(
        {
            "item_code": ["SKU-1", "SKU-1", "SKU-2", "SKU-2"],
            "horizon": [1, 2, 1, 2],
            "predicted_quantity": [0.0, 2.0, 1.0, 3.0],
        }
    )
    quality = summarize_forecast(forecast, expected_skus=2, expected_horizon=2)

    assert quality.forecast_row_count == 4
    assert quality.missing_sku_count == 0
    assert quality.negative_prediction_count == 0
    assert quality.zero_ratio == 0.25


def test_regression_metrics_waits_for_actuals_and_computes_when_present() -> None:
    assert regression_metrics(pd.DataFrame())["status"] == "waiting_for_actuals"
    actuals = pd.DataFrame(
        {"actual_quantity": [1.0, 3.0], "predicted_quantity": [2.0, 3.0]}
    )

    report = regression_metrics(actuals)

    assert report["mae"] == 0.5
    assert report["rmse"] > 0
    assert report["wape"] == 0.25


def test_drift_and_alert_detection_raises_operational_alerts() -> None:
    reference = pd.DataFrame({"prediction": [0.0] * 95 + [1.0] * 5})
    current = pd.DataFrame({"prediction": [0.0] * 5 + [10.0] * 95})
    drift = drift_summary(reference, current, ["prediction"])
    quality = ForecastQuality(1, 1, 1, 1, 1, -2.0, -2.0, -2.0, 0.0)

    alerts = evaluate_alerts(
        quality,
        {"status": "waiting_for_actuals"},
        drift,
        expected_skus=2,
        expected_horizon=56,
    )

    assert drift["drift_detected"] is True
    assert "forecast_missing_skus" in alerts
    assert "negative_predictions" in alerts
    assert "data_or_prediction_drift_detected" in alerts
