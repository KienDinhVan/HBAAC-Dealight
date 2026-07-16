from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from evidently import DataDefinition, Dataset, Report
from evidently.presets import DataDriftPreset
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from hbacc_prj.dataset_config import DatasetConfig
from hbacc_prj.forecasting import build_inference_frame, read_gold_window

DEFAULT_FEATURE_VERSION = "sprint-03-v1-top100-a60-h56"
DEFAULT_HORIZON = 56
DRIFT_THRESHOLD = 0.20
DRIFT_COLUMNS = [
    "horizon",
    "lag_7",
    "lag_28",
    "lag_56",
    "rolling_mean_7",
    "rolling_mean_28",
    "rolling_mean_56",
    "sku_avg_sales",
    "avg_unit_price",
]


@dataclass(frozen=True)
class ForecastQuality:
    forecast_row_count: int
    sku_count: int
    horizon_count: int
    missing_sku_count: int
    negative_prediction_count: int
    prediction_min: float | None
    prediction_mean: float | None
    prediction_max: float | None
    zero_ratio: float | None


def summarize_forecast(
    forecast: pd.DataFrame, expected_skus: int, expected_horizon: int = DEFAULT_HORIZON
) -> ForecastQuality:
    predictions = pd.to_numeric(forecast["predicted_quantity"], errors="coerce")
    sku_count = int(forecast["item_code"].nunique())
    horizon_count = int(forecast["horizon"].nunique())
    return ForecastQuality(
        forecast_row_count=int(len(forecast)),
        sku_count=sku_count,
        horizon_count=horizon_count,
        missing_sku_count=max(expected_skus - sku_count, 0),
        negative_prediction_count=int(predictions.lt(0).sum()),
        prediction_min=float(predictions.min()) if not predictions.empty else None,
        prediction_mean=float(predictions.mean()) if not predictions.empty else None,
        prediction_max=float(predictions.max()) if not predictions.empty else None,
        zero_ratio=float(predictions.eq(0).mean()) if not predictions.empty else None,
    )


def regression_metrics(actuals: pd.DataFrame) -> dict[str, float | int | str]:
    if actuals.empty:
        return {"status": "waiting_for_actuals", "actual_row_count": 0}
    actual = actuals["actual_quantity"].to_numpy(dtype="float64")
    predicted = actuals["predicted_quantity"].to_numpy(dtype="float64")
    error = actual - predicted
    absolute_error = np.abs(error)
    denominator = np.abs(actual).sum()
    smape_denominator = np.abs(actual) + np.abs(predicted)
    return {
        "status": "computed",
        "actual_row_count": int(len(actuals)),
        "mae": float(absolute_error.mean()),
        "rmse": float(np.sqrt(np.square(error).mean())),
        "wape": float(absolute_error.sum() / denominator)
        if denominator
        else 0.0,
        "smape": float(
            np.mean(
                np.divide(
                    2 * absolute_error,
                    smape_denominator,
                    out=np.zeros_like(absolute_error),
                    where=smape_denominator != 0,
                )
            )
        ),
    }


def population_stability_index(reference: pd.Series, current: pd.Series) -> float:
    reference_values = pd.to_numeric(reference, errors="coerce").dropna().to_numpy()
    current_values = pd.to_numeric(current, errors="coerce").dropna().to_numpy()
    if len(reference_values) == 0 or len(current_values) == 0:
        return 0.0
    unique_values = np.unique(reference_values)
    if len(unique_values) < 2:
        return 0.0 if np.array_equal(reference_values, current_values) else 1.0
    if len(unique_values) <= 20:
        boundaries = (unique_values[:-1] + unique_values[1:]) / 2
    else:
        boundaries = np.unique(
            np.quantile(reference_values, np.linspace(0.1, 0.9, 9))
        )
    edges = np.concatenate(([-np.inf], boundaries, [np.inf]))
    reference_counts, _ = np.histogram(reference_values, bins=edges)
    current_counts, _ = np.histogram(current_values, bins=edges)
    reference_ratio = np.maximum(reference_counts / len(reference_values), 1e-6)
    current_ratio = np.maximum(current_counts / len(current_values), 1e-6)
    return float(
        np.sum((current_ratio - reference_ratio) * np.log(current_ratio / reference_ratio))
    )


def drift_summary(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    columns: list[str],
    threshold: float = DRIFT_THRESHOLD,
) -> dict[str, Any]:
    psi_by_column = {
        column: population_stability_index(reference[column], current[column])
        for column in columns
        if column in reference.columns and column in current.columns
    }
    drifted = sorted(
        column for column, score in psi_by_column.items() if score >= threshold
    )
    return {
        "method": "psi",
        "threshold": threshold,
        "checked_columns": len(psi_by_column),
        "drifted_columns": drifted,
        "drifted_column_count": len(drifted),
        "drift_detected": bool(drifted),
        "psi": psi_by_column,
    }


def evaluate_alerts(
    quality: ForecastQuality,
    accuracy: dict[str, float | int | str],
    drift: dict[str, Any],
    expected_skus: int,
    expected_horizon: int = DEFAULT_HORIZON,
) -> list[str]:
    alerts: list[str] = []
    if quality.missing_sku_count:
        alerts.append("forecast_missing_skus")
    if quality.negative_prediction_count:
        alerts.append("negative_predictions")
    if quality.forecast_row_count != expected_skus * expected_horizon:
        alerts.append("forecast_row_count_invalid")
    if quality.horizon_count != expected_horizon:
        alerts.append("forecast_horizon_invalid")
    if drift.get("drift_detected"):
        alerts.append("data_or_prediction_drift_detected")
    if accuracy.get("status") == "computed" and float(accuracy.get("wape", 0)) > 1:
        alerts.append("wape_degradation")
    return alerts


def _read_frame(
    connection: psycopg.Connection[Any], query: str, params: tuple[Any, ...]
) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column.name for column in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def read_latest_run(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT run_id, forecast_date, model_name, model_version, row_count
            FROM serving.forecast_runs
            WHERE status = 'success'
            ORDER BY finished_at DESC NULLS LAST, started_at DESC
            LIMIT 1
            """
        )
        run = cursor.fetchone()
    if run is None:
        raise ValueError("No successful forecast run exists")
    return dict(run)


def read_forecast(connection: psycopg.Connection[Any], run_id: str) -> pd.DataFrame:
    return _read_frame(
        connection,
        """
        SELECT item_code, target_date, horizon, predicted_quantity
        FROM serving.sku_forecast WHERE run_id = %s
        ORDER BY item_code, horizon
        """,
        (run_id,),
    )


def read_expected_sku_count(connection: psycopg.Connection[Any]) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(DISTINCT item_code) FROM gold.daily_sku_sales")
        return int(cursor.fetchone()[0])


def read_actuals(connection: psycopg.Connection[Any], run_id: str) -> pd.DataFrame:
    return _read_frame(
        connection,
        """
        SELECT f.item_code, f.target_date, f.horizon, f.predicted_quantity,
               g.quantity_sold AS actual_quantity
        FROM serving.sku_forecast f
        JOIN gold.daily_sku_sales g
          ON g.item_code = f.item_code AND g.date = f.target_date
        WHERE f.run_id = %s
        """,
        (run_id,),
    )


def read_feature_windows(
    connection: psycopg.Connection[Any], feature_version: str, forecast_date: Any
) -> tuple[pd.DataFrame, pd.DataFrame]:
    reference_query = f"""
        SELECT {", ".join(DRIFT_COLUMNS)}
        FROM features.offline_sku_features
        WHERE feature_version = %s AND as_of_date = (
            SELECT MAX(as_of_date)
            FROM features.offline_sku_features
            WHERE feature_version = %s
        )
        ORDER BY item_code, horizon
    """
    reference = _read_frame(
        connection, reference_query, (feature_version, feature_version)
    )
    sku_frame = _read_frame(
        connection,
        """
        SELECT DISTINCT item_code
        FROM features.offline_sku_features
        WHERE feature_version = %s
        ORDER BY item_code
        """,
        (feature_version,),
    )
    if reference.empty or sku_frame.empty:
        raise ValueError(f"No reference feature window exists for '{feature_version}'")
    skus = sku_frame["item_code"].tolist()
    gold = read_gold_window(connection, forecast_date, lookback_days=200)
    current = build_inference_frame(gold, skus, forecast_date)[DRIFT_COLUMNS]
    return reference, current


def read_prediction_windows(
    connection: psycopg.Connection[Any], latest_run_id: str
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT run_id FROM serving.forecast_runs
            WHERE status = 'success' AND run_id <> %s
            ORDER BY finished_at DESC NULLS LAST, started_at DESC LIMIT 1
            """,
            (latest_run_id,),
        )
        previous = cursor.fetchone()
    if previous is None:
        return None
    query = """
        SELECT predicted_quantity
        FROM serving.sku_forecast WHERE run_id = %s
        ORDER BY item_code, horizon
    """
    return (
        _read_frame(connection, query, (previous[0],)),
        _read_frame(connection, query, (latest_run_id,)),
    )


def create_evidently_report(
    reference: pd.DataFrame, current: pd.DataFrame, path: Path
) -> None:
    definition = DataDefinition()
    reference_dataset = Dataset.from_pandas(reference, data_definition=definition)
    current_dataset = Dataset.from_pandas(current, data_definition=definition)
    result = Report([DataDriftPreset()]).run(current_dataset, reference_dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    result.save_html(str(path))


def save_report(
    connection: psycopg.Connection[Any],
    report_id: str,
    run_id: str,
    quality: ForecastQuality,
    accuracy: dict[str, float | int | str],
    drift: dict[str, Any],
    alerts: list[str],
    data_drift_path: Path,
    prediction_drift_path: Path | None,
) -> None:
    connection.execute(
        """
        INSERT INTO monitoring.forecast_reports (
            report_id, run_id, status, forecast_row_count, sku_count,
            horizon_count, missing_sku_count, negative_prediction_count,
            prediction_min, prediction_mean, prediction_max, zero_ratio,
            actual_row_count, accuracy_metrics, drift_detected, drift_metrics,
            alerts, data_drift_report_path, prediction_drift_report_path
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (report_id) DO UPDATE SET
            generated_at = clock_timestamp(), status = EXCLUDED.status,
            forecast_row_count = EXCLUDED.forecast_row_count,
            sku_count = EXCLUDED.sku_count,
            horizon_count = EXCLUDED.horizon_count,
            missing_sku_count = EXCLUDED.missing_sku_count,
            negative_prediction_count = EXCLUDED.negative_prediction_count,
            prediction_min = EXCLUDED.prediction_min,
            prediction_mean = EXCLUDED.prediction_mean,
            prediction_max = EXCLUDED.prediction_max,
            zero_ratio = EXCLUDED.zero_ratio,
            actual_row_count = EXCLUDED.actual_row_count,
            accuracy_metrics = EXCLUDED.accuracy_metrics,
            drift_detected = EXCLUDED.drift_detected,
            drift_metrics = EXCLUDED.drift_metrics,
            alerts = EXCLUDED.alerts,
            data_drift_report_path = EXCLUDED.data_drift_report_path,
            prediction_drift_report_path = EXCLUDED.prediction_drift_report_path
        """,
        (
            report_id,
            run_id,
            "alert" if alerts else "success",
            quality.forecast_row_count,
            quality.sku_count,
            quality.horizon_count,
            quality.missing_sku_count,
            quality.negative_prediction_count,
            quality.prediction_min,
            quality.prediction_mean,
            quality.prediction_max,
            quality.zero_ratio,
            int(accuracy["actual_row_count"]),
            Jsonb(accuracy),
            bool(drift["drift_detected"]),
            Jsonb(drift),
            Jsonb(alerts),
            str(data_drift_path),
            str(prediction_drift_path) if prediction_drift_path else None,
        ),
    )


def run_monitoring(
    connection: psycopg.Connection[Any],
    report_id: str,
    output_dir: Path,
    feature_version: str = DEFAULT_FEATURE_VERSION,
) -> dict[str, Any]:
    run = read_latest_run(connection)
    expected_skus = read_expected_sku_count(connection)
    forecast = read_forecast(connection, run["run_id"])
    quality = summarize_forecast(forecast, expected_skus)
    accuracy = regression_metrics(read_actuals(connection, run["run_id"]))

    feature_reference, feature_current = read_feature_windows(
        connection, feature_version, run["forecast_date"]
    )
    feature_drift = drift_summary(feature_reference, feature_current, DRIFT_COLUMNS)
    data_drift_path = output_dir / f"{report_id}_data_drift.html"
    create_evidently_report(feature_reference, feature_current, data_drift_path)

    prediction_drift_path: Path | None = None
    prediction_windows = read_prediction_windows(connection, run["run_id"])
    prediction_drift: dict[str, Any] = {
        "method": "psi",
        "checked_columns": 0,
        "drifted_columns": [],
        "drifted_column_count": 0,
        "drift_detected": False,
        "psi": {},
    }
    if prediction_windows is not None:
        previous, current = prediction_windows
        prediction_drift = drift_summary(previous, current, ["predicted_quantity"])
        prediction_drift_path = output_dir / f"{report_id}_prediction_drift.html"
        create_evidently_report(previous, current, prediction_drift_path)

    drift = {
        "drift_detected": bool(
            feature_drift["drift_detected"] or prediction_drift["drift_detected"]
        ),
        "data": feature_drift,
        "prediction": prediction_drift,
    }
    alerts = evaluate_alerts(quality, accuracy, drift, expected_skus)
    save_report(
        connection,
        report_id,
        run["run_id"],
        quality,
        accuracy,
        drift,
        alerts,
        data_drift_path,
        prediction_drift_path,
    )
    return {
        "report_id": report_id,
        "run_id": run["run_id"],
        "feature_version": feature_version,
        "quality": asdict(quality),
        "accuracy": accuracy,
        "drift": drift,
        "alerts": alerts,
        "status": "alert" if alerts else "success",
        "data_drift_report_path": str(data_drift_path),
        "prediction_drift_report_path": str(prediction_drift_path)
        if prediction_drift_path
        else None,
    }


def dump_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _resolve_monitoring_dir() -> Path:
    """Return a writable location for generated monitoring reports."""
    configured = Path(os.environ.get("MONITORING_REPORT_DIR", "data/monitoring"))
    try:
        configured.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=configured):
            pass
        return configured
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / "monitoring-reports"
        fallback.mkdir(parents=True, exist_ok=True)
        logging.getLogger(__name__).warning(
            "Monitoring dir %s not writable (%s); using %s",
            configured,
            exc,
            fallback,
        )
        return fallback


def monitor_dataset(cfg: DatasetConfig) -> dict[str, Any]:
    """Thin per-dataset adapter over `run_monitoring` (Sprint 7 entry point).

    `run_monitoring` reads/writes hardcoded Postgres tables
    (`serving.forecast_runs`, `serving.sku_forecast`, `gold.daily_sku_sales`,
    `features.offline_sku_features`, `monitoring.forecast_reports`); there is
    no table parameter to thread `cfg.table_name` into without restructuring
    those reads, which is out of scope here. What maps cleanly, per the
    brief, is keying the monitoring `report_id` by `cfg.name` so each
    dataset gets its own report row/history. `feature_version`, the schema
    path, `database_url`, and `output_dir` use today's hbaac defaults,
    matching `scripts/run_monitoring.py`.
    """
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://forecast:forecast-local-only@localhost:5432/sku_forecasting",
    )
    schema_path = Path("scripts/sprint_07_monitoring_schema.sql")
    output_dir = _resolve_monitoring_dir()
    report_id = f"{cfg.name}-monitoring"

    with psycopg.connect(database_url) as connection:
        connection.execute(schema_path.read_text(encoding="utf-8"))
        report = run_monitoring(
            connection, report_id, output_dir, DEFAULT_FEATURE_VERSION
        )
    dump_report(report, output_dir / f"{report_id}.json")
    return report
