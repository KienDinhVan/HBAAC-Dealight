from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import psycopg

FeatureStage = Literal["validate-gold", "save", "quality", "summary", "all"]

KEY_COLUMNS = [
    "feature_version",
    "as_of_date",
    "item_code",
    "horizon",
    "target_date",
    "target_quantity",
]
TIME_FEATURE_COLUMNS = [
    "day_of_week",
    "day_of_month",
    "week_of_year",
    "month",
    "quarter",
    "is_weekend",
    "is_month_start",
    "is_month_end",
]
NUMERIC_FEATURE_COLUMNS = [
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
]
FEATURE_COLUMNS = (
    TIME_FEATURE_COLUMNS + NUMERIC_FEATURE_COLUMNS + ["sku_first_sale_date"]
)


def _copy_frame(
    connection: psycopg.Connection[Any],
    table_name: str,
    columns: list[str],
    frame: pd.DataFrame,
) -> None:
    column_sql = ", ".join(columns)
    with connection.cursor().copy(
        f"COPY {table_name} ({column_sql}) FROM STDIN"
    ) as copy:
        for row in frame[columns].itertuples(index=False, name=None):
            copy.write_row(tuple(None if pd.isna(value) else value for value in row))


def read_gold(connection: psycopg.Connection[Any]) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT date, item_code, quantity_sold, return_quantity, avg_unit_price
            FROM gold.daily_sku_sales
            ORDER BY date, item_code
            """
        )
        columns = [description.name for description in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def _dense_daily_panel(gold_frame: pd.DataFrame, max_skus: int) -> pd.DataFrame:
    if gold_frame.empty:
        raise ValueError("Gold table is empty")
    gold = gold_frame.copy()
    gold["date"] = pd.to_datetime(gold["date"])
    for column in ["quantity_sold", "return_quantity", "avg_unit_price"]:
        gold[column] = pd.to_numeric(gold[column], errors="coerce")

    top_skus = (
        gold.groupby("item_code", observed=True)["quantity_sold"]
        .sum()
        .sort_values(ascending=False)
        .head(max_skus)
        .index
    )
    gold = gold.loc[gold["item_code"].isin(top_skus)]
    days = pd.date_range(gold["date"].min(), gold["date"].max(), freq="D")
    index = pd.MultiIndex.from_product([top_skus, days], names=["item_code", "date"])
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


def _compute_historical_features(group: pd.DataFrame) -> pd.DataFrame:
    result = group.copy()
    quantity = result["quantity_sold"].astype("float64")
    returns = result["return_quantity"].astype("float64")
    history = quantity.shift(1, fill_value=0.0)
    return_history = returns.shift(1, fill_value=0.0)

    for lag in [1, 7, 14, 28, 56]:
        result[f"lag_{lag}"] = quantity.shift(lag, fill_value=0.0)
    for window in [7, 14, 28, 56]:
        rolling = history.rolling(window, min_periods=1)
        result[f"rolling_mean_{window}"] = rolling.mean()
        if window in [7, 28]:
            result[f"rolling_std_{window}"] = rolling.std().fillna(0.0)
        if window == 28:
            result["rolling_min_28"] = rolling.min()
            result["rolling_max_28"] = rolling.max()
        if window in [7, 28]:
            result[f"rolling_sum_{window}"] = rolling.sum()

    expanding = history.expanding(min_periods=1)
    result["sku_avg_sales"] = expanding.mean()
    result["sku_median_sales"] = expanding.median()
    result["sku_sales_std"] = expanding.std().fillna(0.0)
    result["sku_total_sales"] = expanding.sum()
    result["sku_nonzero_sales_ratio"] = history.gt(0).expanding().mean()
    total_activity = history.expanding().sum() + return_history.expanding().sum()
    result["sku_return_rate"] = (
        return_history.expanding().sum() / total_activity.replace(0.0, np.nan)
    ).fillna(0.0)

    first_sale_date = result.loc[quantity.gt(0), "date"].min()
    known_sale = result["date"] > first_sale_date
    result["sku_first_sale_date"] = pd.NaT
    result.loc[known_sale, "sku_first_sale_date"] = first_sale_date
    result["sku_lifecycle_days"] = (
        (result["date"] - result["sku_first_sale_date"])
        .dt.days.fillna(0)
        .astype("int32")
    )

    known_price = result["avg_unit_price"].ffill()
    prior_price = known_price.shift(1)
    result["avg_unit_price"] = prior_price
    result["lag_price_1"] = known_price.shift(2)
    result["rolling_price_mean_7"] = prior_price.rolling(7, min_periods=1).mean()
    result["rolling_price_mean_28"] = prior_price.rolling(28, min_periods=1).mean()
    result["price_change_rate"] = (
        prior_price / result["lag_price_1"].replace(0.0, np.nan) - 1.0
    ).fillna(0.0)
    result["discount_proxy"] = (
        prior_price / result["rolling_price_mean_28"].replace(0.0, np.nan) - 1.0
    ).fillna(0.0)
    return result


def build_training_frame(
    gold_frame: pd.DataFrame,
    feature_version: str,
    max_skus: int = 100,
    as_of_days: int = 60,
    max_horizon: int = 56,
) -> pd.DataFrame:
    if max_horizon < 1 or max_horizon > 56:
        raise ValueError("max_horizon must be in 1..56")
    panel = _dense_daily_panel(gold_frame, max_skus)
    history = pd.concat(
        [
            _compute_historical_features(group)
            for _, group in panel.groupby("item_code", observed=True)
        ],
        ignore_index=True,
    )
    last_date = history["date"].max()
    cutoff = last_date - pd.Timedelta(days=max_horizon)
    anchor_start = cutoff - pd.Timedelta(days=as_of_days - 1)
    anchors = history.loc[history["date"].between(anchor_start, cutoff)].copy()
    if anchors.empty:
        raise ValueError("Not enough Gold history for requested training frame")

    horizons = pd.DataFrame({"horizon": range(1, max_horizon + 1)})
    training = anchors.merge(horizons, how="cross")
    training["as_of_date"] = training.pop("date")
    training["target_date"] = training["as_of_date"] + pd.to_timedelta(
        training["horizon"], unit="D"
    )
    target = panel.rename(
        columns={"date": "target_date", "quantity_sold": "target_quantity"}
    )[["item_code", "target_date", "target_quantity"]]
    training = training.merge(
        target, on=["item_code", "target_date"], how="left", validate="many_to_one"
    )
    training["target_quantity"] = training["target_quantity"].fillna(0.0)
    target_date = training["target_date"].dt
    iso_calendar = target_date.isocalendar()
    training["day_of_week"] = target_date.dayofweek.astype("int16")
    training["day_of_month"] = target_date.day.astype("int16")
    training["week_of_year"] = iso_calendar.week.astype("int16")
    training["month"] = target_date.month.astype("int16")
    training["quarter"] = target_date.quarter.astype("int16")
    training["is_weekend"] = training["day_of_week"].ge(5)
    training["is_month_start"] = target_date.is_month_start
    training["is_month_end"] = target_date.is_month_end
    training.insert(0, "feature_version", feature_version)
    output_columns = (
        KEY_COLUMNS
        + TIME_FEATURE_COLUMNS
        + NUMERIC_FEATURE_COLUMNS
        + ["sku_first_sale_date"]
    )
    output = training[output_columns].copy()
    for column in ["as_of_date", "target_date", "sku_first_sale_date"]:
        output[column] = pd.to_datetime(output[column]).dt.date
    return output.sort_values(["as_of_date", "item_code", "horizon"]).reset_index(
        drop=True
    )


def validate_features(frame: pd.DataFrame) -> dict[str, int]:
    numeric = frame[NUMERIC_FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    checks = {
        "rows": len(frame),
        "skus": int(frame["item_code"].nunique()),
        "as_of_dates": int(frame["as_of_date"].nunique()),
        "horizons": int(frame["horizon"].nunique()),
        "null_keys": int(frame[KEY_COLUMNS].isna().any(axis=1).sum()),
        "invalid_horizons": int((~frame["horizon"].between(1, 56)).sum()),
        "negative_targets": int((frame["target_quantity"] < 0).sum()),
        "target_not_future": int(
            (
                pd.to_datetime(frame["target_date"])
                <= pd.to_datetime(frame["as_of_date"])
            ).sum()
        ),
        "duplicate_keys": int(
            frame.duplicated(
                ["feature_version", "as_of_date", "item_code", "horizon"]
            ).sum()
        ),
        "infinite_features": int(np.isinf(numeric.to_numpy(dtype="float64")).sum()),
    }
    failed = {
        key: value
        for key, value in checks.items()
        if key
        in {
            "null_keys",
            "invalid_horizons",
            "negative_targets",
            "target_not_future",
            "duplicate_keys",
            "infinite_features",
        }
        and value
    }
    if failed:
        raise ValueError(f"Feature quality checks failed: {failed}")
    return checks


def persist_features(
    connection: psycopg.Connection[Any], feature_version: str, frame: pd.DataFrame
) -> None:
    connection.execute(
        "DELETE FROM features.offline_sku_features WHERE feature_version = %s",
        (feature_version,),
    )
    _copy_frame(connection, "features.offline_sku_features", list(frame.columns), frame)


def read_persisted_features(
    connection: psycopg.Connection[Any], feature_version: str
) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM features.offline_sku_features
            WHERE feature_version = %s
            ORDER BY as_of_date, item_code, horizon
            """,
            (feature_version,),
        )
        columns = [description.name for description in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def run_feature_stage(
    database_url: str,
    schema_path: Path,
    feature_version: str,
    max_skus: int,
    as_of_days: int,
    max_horizon: int,
    stage: FeatureStage = "all",
) -> dict[str, int | str]:
    with psycopg.connect(database_url) as connection:
        connection.execute(schema_path.read_text(encoding="utf-8"))
        gold = read_gold(connection)
        if stage == "validate-gold":
            return {"gold_rows": len(gold)}
        if stage in {"quality", "summary"}:
            features = read_persisted_features(connection, feature_version)
            summary = validate_features(features)
            summary["feature_version"] = feature_version
            return summary
        features = build_training_frame(
            gold,
            feature_version=feature_version,
            max_skus=max_skus,
            as_of_days=as_of_days,
            max_horizon=max_horizon,
        )
        summary = validate_features(features)
        persist_features(connection, feature_version, features)
        summary["feature_version"] = feature_version
        return summary
