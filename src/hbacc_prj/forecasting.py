from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import mlflow
import mlflow.lightgbm
import numpy as np
import pandas as pd
import psycopg
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from hbacc_prj.features import (
    NUMERIC_FEATURE_COLUMNS,
    TIME_FEATURE_COLUMNS,
    _compute_historical_features,
)
from hbacc_prj.training import MODEL_FEATURE_COLUMNS, clip_negative_predictions

DEFAULT_MAX_HORIZON = 56
DEFAULT_LOOKBACK_DAYS = 730
PREDICTION_SOURCE_LIGHTGBM = "lightgbm"
PREDICTION_SOURCE_BASELINE = "seasonal_naive_lag_7"


@dataclass(frozen=True)
class LoadedModel:
    model: Any
    name: str
    version: str
    stage: str
    training_skus: frozenset[str]


def load_production_model(
    tracking_uri: str,
    model_name: str,
    training_skus: list[str] | None = None,
) -> LoadedModel:
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    selected_version: str | None = None
    selected_stage: str | None = None
    for stage in ("Production", "Staging"):
        try:
            versions = client.get_latest_versions(model_name, stages=[stage])
        except MlflowException:
            versions = []
        if versions:
            selected_version = versions[0].version
            selected_stage = stage
            break
    if selected_version is None or selected_stage is None:
        raise ValueError(
            f"No Production or Staging version for model '{model_name}'"
        )
    model = mlflow.lightgbm.load_model(
        f"models:/{model_name}/{selected_version}"
    )
    return LoadedModel(
        model=model,
        name=model_name,
        version=selected_version,
        stage=selected_stage,
        training_skus=frozenset(training_skus or []),
    )


def read_active_skus(
    connection: psycopg.Connection[Any],
    lookback_days: int | None = DEFAULT_LOOKBACK_DAYS,
) -> list[str]:
    if lookback_days is None or lookback_days <= 0:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT item_code FROM gold.daily_sku_sales
                ORDER BY item_code
                """
            )
            return [row[0] for row in cursor.fetchall()]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH bounds AS (
                SELECT MAX(date) AS last_date FROM gold.daily_sku_sales
            )
            SELECT DISTINCT item_code
            FROM gold.daily_sku_sales, bounds
            WHERE date >= bounds.last_date - (%s::int - 1) * INTERVAL '1 day'
            ORDER BY item_code
            """,
            (lookback_days,),
        )
        return [row[0] for row in cursor.fetchall()]


def read_gold_window(
    connection: psycopg.Connection[Any],
    forecast_date: date,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT date, item_code, quantity_sold, return_quantity, avg_unit_price
            FROM gold.daily_sku_sales
            WHERE date BETWEEN %s::date - (%s::int - 1) * INTERVAL '1 day'
                          AND %s::date
            ORDER BY date, item_code
            """,
            (forecast_date, lookback_days, forecast_date),
        )
        columns = [description.name for description in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def _dense_panel_for_skus(
    gold_frame: pd.DataFrame, skus: list[str], forecast_date: date
) -> pd.DataFrame:
    gold = gold_frame.copy()
    if gold.empty:
        raise ValueError("Gold window is empty")
    gold["date"] = pd.to_datetime(gold["date"])
    for column in ("quantity_sold", "return_quantity", "avg_unit_price"):
        gold[column] = pd.to_numeric(gold[column], errors="coerce")
    gold = gold.loc[gold["item_code"].isin(skus)]
    last_day = pd.Timestamp(forecast_date)
    days = pd.date_range(gold["date"].min(), last_day, freq="D")
    index = pd.MultiIndex.from_product(
        [sorted(skus), days], names=["item_code", "date"]
    )
    panel = (
        gold.set_index(["item_code", "date"])[
            ["quantity_sold", "return_quantity", "avg_unit_price"]
        ]
        .reindex(index)
        .reset_index()
    )
    panel[["quantity_sold", "return_quantity"]] = panel[
        ["quantity_sold", "return_quantity"]
    ].fillna(0.0)
    return panel.sort_values(["item_code", "date"]).reset_index(drop=True)


def build_inference_frame(
    gold_frame: pd.DataFrame,
    skus: list[str],
    forecast_date: date,
    max_horizon: int = DEFAULT_MAX_HORIZON,
) -> pd.DataFrame:
    if max_horizon < 1 or max_horizon > 56:
        raise ValueError("max_horizon must be in 1..56")
    if not skus:
        raise ValueError("skus list is empty")
    panel = _dense_panel_for_skus(gold_frame, skus, forecast_date)
    history = pd.concat(
        [
            _compute_historical_features(group)
            for _, group in panel.groupby("item_code", observed=True)
        ],
        ignore_index=True,
    )
    anchor_ts = pd.Timestamp(forecast_date)
    anchors = history.loc[history["date"] == anchor_ts].copy()
    if anchors.empty:
        raise ValueError(
            f"No anchor rows for forecast_date={forecast_date.isoformat()}"
        )
    horizons = pd.DataFrame({"horizon": range(1, max_horizon + 1)})
    frame = anchors.merge(horizons, how="cross")
    frame["as_of_date"] = frame.pop("date")
    frame["target_date"] = frame["as_of_date"] + pd.to_timedelta(
        frame["horizon"], unit="D"
    )
    target_date = frame["target_date"].dt
    iso_calendar = target_date.isocalendar()
    frame["day_of_week"] = target_date.dayofweek.astype("int16")
    frame["day_of_month"] = target_date.day.astype("int16")
    frame["week_of_year"] = iso_calendar.week.astype("int16")
    frame["month"] = target_date.month.astype("int16")
    frame["quarter"] = target_date.quarter.astype("int16")
    frame["is_weekend"] = frame["day_of_week"].ge(5)
    frame["is_month_start"] = target_date.is_month_start
    frame["is_month_end"] = target_date.is_month_end
    frame["forecast_date"] = pd.Timestamp(forecast_date).date()
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"]).dt.date
    frame["target_date"] = pd.to_datetime(frame["target_date"]).dt.date
    output_columns = (
        [
            "forecast_date",
            "item_code",
            "as_of_date",
            "target_date",
            "horizon",
        ]
        + TIME_FEATURE_COLUMNS
        + NUMERIC_FEATURE_COLUMNS
    )
    return (
        frame[output_columns]
        .sort_values(["item_code", "horizon"])
        .reset_index(drop=True)
    )


def _transform_for_lightgbm(
    frame: pd.DataFrame, training_skus: list[str]
) -> pd.DataFrame:
    mapping = {sku: index for index, sku in enumerate(sorted(training_skus))}
    x = frame[TIME_FEATURE_COLUMNS + NUMERIC_FEATURE_COLUMNS].copy()
    for column in TIME_FEATURE_COLUMNS:
        x[column] = x[column].astype("int8")
    x[NUMERIC_FEATURE_COLUMNS] = (
        x[NUMERIC_FEATURE_COLUMNS]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )
    x["item_code_id"] = (
        frame["item_code"].map(mapping).fillna(-1).astype("int32")
    )
    return x[MODEL_FEATURE_COLUMNS]


def predict_with_fallback(
    frame: pd.DataFrame,
    model: Any,
    training_skus: frozenset[str],
) -> tuple[np.ndarray, np.ndarray]:
    is_known = frame["item_code"].isin(training_skus).to_numpy()
    predictions = np.zeros(len(frame), dtype="float64")
    sources = np.full(len(frame), PREDICTION_SOURCE_BASELINE, dtype=object)
    if is_known.any():
        known_frame = frame.loc[is_known].reset_index(drop=True)
        x = _transform_for_lightgbm(known_frame, sorted(training_skus))
        known_pred = clip_negative_predictions(model.predict(x))
        predictions[is_known] = known_pred
        sources[is_known] = PREDICTION_SOURCE_LIGHTGBM
    baseline = clip_negative_predictions(
        frame["lag_7"].to_numpy(dtype="float64")
    )
    predictions[~is_known] = baseline[~is_known]
    return predictions, sources


def validate_forecast(
    frame: pd.DataFrame,
    expected_skus: list[str],
    max_horizon: int = DEFAULT_MAX_HORIZON,
) -> dict[str, int]:
    checks = {
        "rows": int(len(frame)),
        "skus": int(frame["item_code"].nunique()),
        "expected_skus": int(len(expected_skus)),
        "missing_skus": int(
            len(set(expected_skus) - set(frame["item_code"].unique()))
        ),
        "horizons": int(frame["horizon"].nunique()),
        "invalid_horizons": int(
            (~frame["horizon"].between(1, max_horizon)).sum()
        ),
        "negative_predictions": int((frame["predicted_quantity"] < 0).sum()),
        "null_predictions": int(frame["predicted_quantity"].isna().sum()),
        "duplicate_keys": int(
            frame.duplicated(["item_code", "target_date"]).sum()
        ),
        "wrong_target_date": int(
            (
                pd.to_datetime(frame["target_date"])
                - pd.to_datetime(frame["forecast_date"])
            )
            .dt.days.ne(frame["horizon"])
            .sum()
        ),
    }
    failed = {
        key: value
        for key, value in checks.items()
        if key
        in {
            "missing_skus",
            "invalid_horizons",
            "negative_predictions",
            "null_predictions",
            "duplicate_keys",
            "wrong_target_date",
        }
        and value
    }
    if failed:
        raise ValueError(f"Forecast validation failed: {failed}")
    return checks


def _ensure_forecast_run(
    connection: psycopg.Connection[Any],
    run_id: str,
    forecast_date: date,
    model_name: str,
    model_version: str,
) -> None:
    connection.execute(
        """
        INSERT INTO serving.forecast_runs (
            run_id, forecast_date, model_name, model_version, status,
            row_count, started_at, finished_at, error_message
        )
        VALUES (%s, %s, %s, %s, 'running', NULL, clock_timestamp(), NULL, NULL)
        ON CONFLICT (run_id) DO UPDATE SET
            forecast_date = EXCLUDED.forecast_date,
            model_name = EXCLUDED.model_name,
            model_version = EXCLUDED.model_version,
            status = 'running',
            row_count = NULL,
            started_at = clock_timestamp(),
            finished_at = NULL,
            error_message = NULL
        """,
        (run_id, forecast_date, model_name, model_version),
    )


def persist_forecast(
    connection: psycopg.Connection[Any],
    run_id: str,
    forecast_date: date,
    model_name: str,
    model_version: str,
    frame: pd.DataFrame,
) -> int:
    connection.execute(
        "DELETE FROM serving.sku_forecast WHERE run_id = %s", (run_id,)
    )
    with connection.cursor().copy(
        """
        COPY serving.sku_forecast (
            run_id, forecast_date, item_code, target_date, horizon,
            predicted_quantity, model_name, model_version, prediction_source
        ) FROM STDIN
        """
    ) as copy:
        for record in frame.itertuples(index=False):
            copy.write_row(
                (
                    run_id,
                    forecast_date,
                    record.item_code,
                    record.target_date,
                    int(record.horizon),
                    float(record.predicted_quantity),
                    model_name,
                    model_version,
                    record.prediction_source,
                )
            )
    return int(len(frame))


def _mark_run_failure(database_url: str, run_id: str, error: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            UPDATE serving.forecast_runs
            SET status = 'failed', finished_at = clock_timestamp(),
                error_message = %s
            WHERE run_id = %s
            """,
            (error[:2000], run_id),
        )


def _read_training_skus(
    connection: psycopg.Connection[Any], feature_version: str
) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT item_code
            FROM features.offline_sku_features
            WHERE feature_version = %s
            ORDER BY item_code
            """,
            (feature_version,),
        )
        return [row[0] for row in cursor.fetchall()]


def generate_and_save(
    database_url: str,
    tracking_uri: str,
    model_name: str,
    feature_version: str,
    forecast_date: date,
    run_id: str,
    max_horizon: int = DEFAULT_MAX_HORIZON,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    sku_lookback_days: int | None = None,
) -> dict[str, Any]:
    sku_window = (
        sku_lookback_days if sku_lookback_days is not None else lookback_days
    )
    with psycopg.connect(database_url) as connection:
        training_skus = _read_training_skus(connection, feature_version)
        active_skus = read_active_skus(connection, sku_window)
        gold = read_gold_window(connection, forecast_date, lookback_days)

    loaded = load_production_model(
        tracking_uri, model_name, training_skus=training_skus
    )

    frame = build_inference_frame(gold, active_skus, forecast_date, max_horizon)
    predictions, sources = predict_with_fallback(
        frame, loaded.model, loaded.training_skus
    )
    frame["predicted_quantity"] = predictions
    frame["prediction_source"] = sources

    output = frame[
        [
            "forecast_date",
            "item_code",
            "target_date",
            "horizon",
            "predicted_quantity",
            "prediction_source",
        ]
    ].copy()

    validation = validate_forecast(output, active_skus, max_horizon)

    rows = 0
    try:
        with psycopg.connect(database_url) as connection:
            with connection.transaction():
                _ensure_forecast_run(
                    connection,
                    run_id,
                    forecast_date,
                    loaded.name,
                    loaded.version,
                )
                rows = persist_forecast(
                    connection,
                    run_id,
                    forecast_date,
                    loaded.name,
                    loaded.version,
                    output,
                )
                connection.execute(
                    """
                    UPDATE serving.forecast_runs
                    SET status = 'success', row_count = %s,
                        finished_at = clock_timestamp()
                    WHERE run_id = %s
                    """,
                    (rows, run_id),
                )
    except Exception as exc:
        _mark_run_failure(database_url, run_id, repr(exc))
        raise

    source_counts = output["prediction_source"].value_counts().to_dict()
    return {
        "run_id": run_id,
        "forecast_date": forecast_date.isoformat(),
        "model_name": loaded.name,
        "model_version": loaded.version,
        "model_stage": loaded.stage,
        "feature_version": feature_version,
        "training_skus": len(training_skus),
        "active_skus": len(active_skus),
        "max_horizon": max_horizon,
        "lookback_days": lookback_days,
        "rows_written": rows,
        "validation": validation,
        "prediction_source_counts": {
            str(key): int(value) for key, value in source_counts.items()
        },
    }
