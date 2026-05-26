from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def parse_vn_decimal(series: pd.Series) -> pd.Series:
    """Parse Vietnamese/European decimal strings such as '12.345,67'."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype("float64")

    cleaned = (
        series.astype("string")
        .str.strip()
        .str.replace(" ", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def load_train(path: str | Path = "data/raw/train.csv") -> pd.DataFrame:
    df = pd.read_csv(
        path,
        dtype={"ItemCode": "string", "UnitPrice": "string", "Unit Cost": "string"},
        parse_dates=["Date"],
        low_memory=False,
    )
    df["ItemCode"] = df["ItemCode"].astype("string")
    df["Quantity"] = (
        pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).astype("int64")
    )
    df["SalesAmount"] = parse_vn_decimal(df["SalesAmount"]).fillna(0).astype("float64")
    df["Cost Amount"] = parse_vn_decimal(df["Cost Amount"]).fillna(0).astype("float64")
    df["UnitPrice_float"] = parse_vn_decimal(df["UnitPrice"])
    df["UnitCost_float"] = parse_vn_decimal(df["Unit Cost"])
    df["is_return"] = (
        (df["Quantity"] < 0) & (df["SalesAmount"] < 0) & (df["Cost Amount"] < 0)
    )
    df["sales_qty"] = df["Quantity"].clip(lower=0).astype("float32")
    df["return_qty"] = np.where(df["is_return"], -df["Quantity"], 0).astype("float32")
    df["net_qty"] = df["Quantity"].astype("float32")
    df["profit"] = (df["SalesAmount"] - df["Cost Amount"]).astype("float64")
    return df


def make_daily_sales(train: pd.DataFrame) -> pd.DataFrame:
    agg = (
        train.groupby(["Date", "ItemCode"], observed=True)
        .agg(
            sales_qty=("sales_qty", "sum"),
            return_qty=("return_qty", "sum"),
            net_qty=("net_qty", "sum"),
            sales_amount=("SalesAmount", "sum"),
            cost_amount=("Cost Amount", "sum"),
            profit=("profit", "sum"),
            line_count=("Quantity", "size"),
        )
        .reset_index()
    )
    return agg.sort_values(["ItemCode", "Date"]).reset_index(drop=True)


def make_demand_matrix(
    daily: pd.DataFrame,
    item_codes: pd.Index | None = None,
    dates: pd.DatetimeIndex | None = None,
    target: str = "sales_qty",
) -> pd.DataFrame:
    if item_codes is None:
        item_codes = pd.Index(
            sorted(daily["ItemCode"].astype(str).unique()), name="ItemCode"
        )
    if dates is None:
        dates = pd.date_range(daily["Date"].min(), daily["Date"].max(), freq="D")

    matrix = daily.pivot_table(
        index="ItemCode",
        columns="Date",
        values=target,
        aggfunc="sum",
        fill_value=0,
        observed=True,
    )
    matrix.index = matrix.index.astype(str)
    matrix = matrix.reindex(index=item_codes.astype(str), columns=dates, fill_value=0)
    return matrix.fillna(0).astype("float32")


def make_sku_profile_from_daily(
    daily: pd.DataFrame,
    y: pd.DataFrame,
    as_of: pd.Timestamp | str | None = None,
) -> pd.DataFrame:
    if as_of is not None:
        as_of = pd.Timestamp(as_of)
        daily = daily.loc[daily["Date"] <= as_of]
        y = y.loc[:, pd.DatetimeIndex(y.columns) <= as_of]

    sku_profit = daily.groupby("ItemCode", observed=True)["profit"].sum()
    sku_return_qty = daily.groupby("ItemCode", observed=True)["return_qty"].sum()
    sku_sales_qty = daily.groupby("ItemCode", observed=True)["sales_qty"].sum()
    sku_profit.index = sku_profit.index.astype(str)
    sku_return_qty.index = sku_return_qty.index.astype(str)
    sku_sales_qty.index = sku_sales_qty.index.astype(str)

    nonzero = y.gt(0)
    profile = pd.DataFrame(index=y.index)
    profile["total_sales_qty"] = y.sum(axis=1).astype("float64")
    profile["active_days"] = nonzero.sum(axis=1).astype("int32")
    profile["zero_ratio"] = 1.0 - profile["active_days"] / y.shape[1]
    profile["avg_daily_qty"] = y.mean(axis=1).astype("float64")
    profile["avg_qty_when_active"] = (
        profile["total_sales_qty"] / profile["active_days"].replace(0, np.nan)
    ).fillna(0)
    profile["total_profit"] = sku_profit.reindex(y.index).fillna(0).astype("float64")
    profile["positive_profit"] = profile["total_profit"].clip(lower=0)
    profit_sum = profile["positive_profit"].sum()
    profile["profit_weight"] = (
        profile["positive_profit"] / profit_sum if profit_sum > 0 else 0.0
    )
    profile["return_qty"] = sku_return_qty.reindex(y.index).fillna(0).astype("float64")
    profile["return_ratio"] = (
        profile["return_qty"] / sku_sales_qty.reindex(y.index).replace(0, np.nan)
    ).fillna(0)

    columns = pd.DatetimeIndex(y.columns)
    last_sale_dates = []
    for values in y.to_numpy():
        pos = np.flatnonzero(values > 0)
        last_sale_dates.append(columns[pos[-1]] if len(pos) else pd.NaT)
    profile["last_sale_date"] = last_sale_dates
    profile["days_since_last_sale"] = (
        columns.max() - pd.to_datetime(profile["last_sale_date"])
    ).dt.days
    profile["days_since_last_sale"] = (
        profile["days_since_last_sale"].fillna(9999).astype("int32")
    )
    return profile


def make_sku_profile(train: pd.DataFrame, y: pd.DataFrame) -> pd.DataFrame:
    daily = make_daily_sales(train)
    return make_sku_profile_from_daily(daily, y)
