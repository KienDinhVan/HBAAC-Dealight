from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest

from hbacc_prj.forecasting import (
    DEFAULT_MAX_HORIZON,
    PREDICTION_SOURCE_BASELINE,
    PREDICTION_SOURCE_LIGHTGBM,
    _ensure_forecast_run,
    build_inference_frame,
    persist_forecast,
    predict_with_fallback,
    validate_forecast,
)

FORECAST_DATE = date(2025, 9, 5)
SCHEMA_FILE = Path("scripts/sprint_05_serving_schema.sql")


def _synthetic_gold(skus: list[str], days: int = 90) -> pd.DataFrame:
    dates = pd.date_range(end=pd.Timestamp(FORECAST_DATE), periods=days, freq="D")
    rows: list[dict[str, object]] = []
    for sku_index, sku in enumerate(skus):
        for day_index, day in enumerate(dates):
            quantity = float((day_index + sku_index * 3) % 7)
            rows.append(
                {
                    "date": day.date(),
                    "item_code": sku,
                    "quantity_sold": quantity,
                    "return_quantity": 0.0,
                    "avg_unit_price": 100.0 + sku_index,
                }
            )
    return pd.DataFrame(rows)


def test_build_inference_frame_covers_horizons_and_target_dates() -> None:
    skus = ["SKU-A", "SKU-B", "SKU-C"]
    gold = _synthetic_gold(skus)
    frame = build_inference_frame(gold, skus, FORECAST_DATE)

    assert set(frame["item_code"].unique()) == set(skus)
    assert frame["horizon"].min() == 1
    assert frame["horizon"].max() == DEFAULT_MAX_HORIZON
    assert (frame.groupby("item_code")["horizon"].nunique() == 56).all()

    delta = (
        pd.to_datetime(frame["target_date"]) - pd.to_datetime(frame["forecast_date"])
    ).dt.days
    assert (delta == frame["horizon"]).all()
    assert (frame["as_of_date"] == FORECAST_DATE).all()


def test_predict_with_fallback_uses_baseline_for_unknown_skus() -> None:
    skus = ["SKU-A", "SKU-B"]
    gold = _synthetic_gold(skus)
    frame = build_inference_frame(gold, skus, FORECAST_DATE)

    class _StubModel:
        def predict(self, x: pd.DataFrame) -> np.ndarray:
            return np.full(len(x), 9.0, dtype="float64")

    predictions, sources = predict_with_fallback(
        frame, _StubModel(), frozenset({"SKU-A"})
    )

    assert (predictions >= 0).all()
    known_mask = frame["item_code"].eq("SKU-A").to_numpy()
    unknown_mask = ~known_mask
    assert (sources[known_mask] == PREDICTION_SOURCE_LIGHTGBM).all()
    assert (sources[unknown_mask] == PREDICTION_SOURCE_BASELINE).all()
    assert (predictions[known_mask] == 9.0).all()
    expected_baseline = np.clip(
        frame.loc[unknown_mask, "lag_7"].to_numpy(dtype="float64"), 0.0, None
    )
    assert np.allclose(predictions[unknown_mask], expected_baseline)


def _forecast_output(skus: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for sku in skus:
        for horizon in range(1, DEFAULT_MAX_HORIZON + 1):
            rows.append(
                {
                    "forecast_date": FORECAST_DATE,
                    "item_code": sku,
                    "target_date": (
                        pd.Timestamp(FORECAST_DATE) + pd.Timedelta(days=horizon)
                    ).date(),
                    "horizon": horizon,
                    "predicted_quantity": float(horizon % 5),
                    "prediction_source": PREDICTION_SOURCE_LIGHTGBM,
                }
            )
    return pd.DataFrame(rows)


def test_validate_forecast_passes_for_clean_frame() -> None:
    skus = ["SKU-A", "SKU-B"]
    frame = _forecast_output(skus)
    checks = validate_forecast(frame, skus)

    assert checks["rows"] == len(skus) * DEFAULT_MAX_HORIZON
    assert checks["missing_skus"] == 0
    assert checks["negative_predictions"] == 0
    assert checks["duplicate_keys"] == 0
    assert checks["wrong_target_date"] == 0


def test_validate_forecast_flags_missing_sku_and_duplicates() -> None:
    frame = _forecast_output(["SKU-A"])
    with pytest.raises(ValueError) as excinfo:
        validate_forecast(frame, ["SKU-A", "SKU-MISSING"])
    assert "missing_skus" in str(excinfo.value)

    duplicate_frame = pd.concat(
        [_forecast_output(["SKU-A"]), _forecast_output(["SKU-A"]).head(1)],
        ignore_index=True,
    )
    with pytest.raises(ValueError) as excinfo:
        validate_forecast(duplicate_frame, ["SKU-A"])
    assert "duplicate_keys" in str(excinfo.value)


def test_validate_forecast_flags_negative_prediction() -> None:
    frame = _forecast_output(["SKU-A"])
    frame.loc[0, "predicted_quantity"] = -1.0
    with pytest.raises(ValueError) as excinfo:
        validate_forecast(frame, ["SKU-A"])
    assert "negative_predictions" in str(excinfo.value)


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="Integration test requires DATABASE_URL.",
)
def test_persist_forecast_is_idempotent() -> None:
    database_url = os.environ["DATABASE_URL"]
    run_id = "test-sprint-05-idempotent"
    skus = ["TEST-SKU-A", "TEST-SKU-B"]
    frame = _forecast_output(skus)

    with psycopg.connect(database_url) as connection:
        connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        with connection.transaction():
            _ensure_forecast_run(
                connection, run_id, FORECAST_DATE, "test-model", "test-version"
            )
            first_rows = persist_forecast(
                connection,
                run_id,
                FORECAST_DATE,
                "test-model",
                "test-version",
                frame,
            )
        with connection.transaction():
            _ensure_forecast_run(
                connection, run_id, FORECAST_DATE, "test-model", "test-version"
            )
            second_rows = persist_forecast(
                connection,
                run_id,
                FORECAST_DATE,
                "test-model",
                "test-version",
                frame,
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM serving.sku_forecast WHERE run_id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == first_rows == second_rows == len(frame)
        with connection.transaction():
            connection.execute(
                "DELETE FROM serving.forecast_runs WHERE run_id = %s",
                (run_id,),
            )
