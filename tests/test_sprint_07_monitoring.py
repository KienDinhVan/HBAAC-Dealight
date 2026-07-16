from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from hbacc_prj.monitoring import (
    ForecastQuality,
    _publish_monitoring_report,
    _resolve_monitoring_dir,
    drift_summary,
    evaluate_alerts,
    regression_metrics,
    summarize_forecast,
)


def test_publish_monitoring_report_uses_local_path_without_gcs(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "report.html"
    report.write_text("report")
    monkeypatch.delenv("GCS_BUCKET", raising=False)

    assert _publish_monitoring_report(report) == str(report)


def test_publish_monitoring_report_uploads_to_gcs(monkeypatch, tmp_path) -> None:
    report = tmp_path / "report.html"
    report.write_text("report")
    uploads = []

    class Blob:
        def upload_from_filename(self, filename, content_type):
            uploads.append((filename, content_type))

    class Bucket:
        def blob(self, name):
            assert name == "monitoring/report.html"
            return Blob()

    class Client:
        def bucket(self, name):
            assert name == "data-bucket"
            return Bucket()

    monkeypatch.setenv("GCS_BUCKET", "data-bucket")
    monkeypatch.setattr("hbacc_prj.monitoring.storage.Client", Client)

    uri = _publish_monitoring_report(report)

    assert uri == "gs://data-bucket/monitoring/report.html"
    assert uploads == [(str(report), "text/html")]


def test_monitoring_dir_honors_env(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "monitoring"
    monkeypatch.setenv("MONITORING_REPORT_DIR", str(configured))

    assert _resolve_monitoring_dir() == configured
    assert configured.exists()


def test_monitoring_dir_falls_back_when_unwritable(monkeypatch) -> None:
    monkeypatch.setenv("MONITORING_REPORT_DIR", "/proc/not-writable/monitoring")

    resolved = _resolve_monitoring_dir()

    assert resolved.exists()
    assert str(resolved).startswith(tempfile.gettempdir())


def test_monitoring_dir_falls_back_when_existing_dir_is_read_only(
    monkeypatch, tmp_path
) -> None:
    configured = tmp_path / "monitoring"
    configured.mkdir()
    real_named_temporary_file = tempfile.NamedTemporaryFile

    def named_temporary_file(*args, **kwargs):
        if Path(kwargs["dir"]) == configured:
            raise PermissionError("read-only directory")
        return real_named_temporary_file(*args, **kwargs)

    monkeypatch.setenv("MONITORING_REPORT_DIR", str(configured))
    monkeypatch.setattr(
        "hbacc_prj.monitoring.tempfile.NamedTemporaryFile", named_temporary_file
    )

    resolved = _resolve_monitoring_dir()

    assert resolved != configured
    assert str(resolved).startswith(tempfile.gettempdir())


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
