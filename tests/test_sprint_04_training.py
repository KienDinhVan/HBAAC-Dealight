import numpy as np
import pandas as pd

from hbacc_prj.training import (
    build_lgb_dataset,
    calculate_smape,
    calculate_wape,
    clip_negative_predictions,
    fit_lightgbm,
    time_based_split,
)


def _frame() -> pd.DataFrame:
    days = pd.date_range("2025-01-01", periods=50, freq="D")
    rows = []
    for sku_index, sku in enumerate(["SKU-A", "SKU-B"]):
        for day_index, day in enumerate(days):
            row = {
                "item_code": sku,
                "target_date": day.date(),
                "target_quantity": float((day_index + sku_index) % 7),
                "horizon": 1,
            }
            for column in [
                "day_of_week",
                "day_of_month",
                "week_of_year",
                "month",
                "quarter",
                "is_weekend",
                "is_month_start",
                "is_month_end",
            ]:
                row[column] = 0
            for column in [
                "lag_1",
                "lag_7",
                "lag_14",
                "lag_28",
                "lag_56",
                "rolling_mean_7",
                "rolling_mean_14",
                "rolling_mean_28",
                "rolling_mean_56",
                "rolling_std_7",
                "rolling_std_28",
                "rolling_min_28",
                "rolling_max_28",
                "rolling_sum_7",
                "rolling_sum_28",
                "sku_avg_sales",
                "sku_median_sales",
                "sku_sales_std",
                "sku_total_sales",
                "sku_nonzero_sales_ratio",
                "sku_return_rate",
                "sku_lifecycle_days",
                "avg_unit_price",
                "lag_price_1",
                "rolling_price_mean_7",
                "rolling_price_mean_28",
                "price_change_rate",
                "discount_proxy",
            ]:
                row[column] = float(day_index % 7)
            rows.append(row)
    return pd.DataFrame(rows)


def test_time_split_is_temporal_and_metrics_are_correct() -> None:
    train, validation, start = time_based_split(_frame(), validation_days=10)

    assert train["target_date"].max() < start
    assert validation["target_date"].min() >= start
    assert calculate_wape(np.array([1.0, 3.0]), np.array([2.0, 2.0])) == 0.5
    assert calculate_smape(np.array([0.0, 2.0]), np.array([0.0, 2.0])) == 0.0
    assert clip_negative_predictions(np.array([-1.0, 2.0])).tolist() == [0.0, 2.0]


def test_lightgbm_trains_and_predicts_nonnegative_values() -> None:
    train, validation, _ = time_based_split(_frame(), validation_days=10)
    x_train, x_validation, y_train, y_validation = build_lgb_dataset(train, validation)
    model, prediction = fit_lightgbm(
        x_train, y_train, x_validation, y_validation, random_seed=2026
    )

    assert len(prediction) == len(validation)
    assert not np.isnan(prediction).any()
    assert (prediction >= 0).all()
    assert model.booster_ is not None
