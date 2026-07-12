"""
DuckDB client that loads Dealight retail data into an in-memory database.

Tables registered:
  sales      — daily transaction records (from train.csv)
  forecasts  — 56-day demand forecasts per SKU (from submission CSV)
  sku_profile — per-SKU summary stats (active_days, zero_ratio, profit_weight, etc.)
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row

_logger = logging.getLogger(__name__)


def _load_forecasts_from_postgres(database_url: str) -> pd.DataFrame:
    """Hydrate forecasts table from HBAAC Postgres serving.sku_forecast (latest success run).

    Maps Postgres columns to the reference schema used by ForecastAgent:
      item_code         -> sku
      target_date       -> forecast_date
      horizon           -> day_offset
      predicted_quantity-> forecast_qty
    """
    empty_cols = ["sku", "forecast_date", "day_offset", "forecast_qty"]
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.item_code AS sku,
                       f.target_date AS forecast_date,
                       f.horizon AS day_offset,
                       f.predicted_quantity AS forecast_qty
                FROM serving.sku_forecast f
                JOIN (
                    SELECT run_id
                    FROM serving.forecast_runs
                    WHERE status = 'success'
                    ORDER BY finished_at DESC NULLS LAST, started_at DESC
                    LIMIT 1
                ) r ON r.run_id = f.run_id
                """
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=empty_cols)
        df = pd.DataFrame(rows)
        df["forecast_date"] = pd.to_datetime(df["forecast_date"])
        df["day_offset"] = df["day_offset"].astype("int64")
        df["forecast_qty"] = df["forecast_qty"].astype("float64").clip(lower=0)
        return df
    except Exception:  # noqa: BLE001 — fail soft so the API still boots without Postgres
        _logger.exception("Failed to load forecasts from Postgres — forecasts table will be empty")
        return pd.DataFrame(columns=empty_cols)


def _parse_vn_decimal(series: pd.Series) -> pd.Series:
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


def _load_train(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        dtype={"ItemCode": "string", "UnitPrice": "string", "Unit Cost": "string"},
        parse_dates=["Date"],
        low_memory=False,
    )
    df["ItemCode"] = df["ItemCode"].astype("string")
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).astype("int64")
    df["SalesAmount"] = _parse_vn_decimal(df["SalesAmount"]).fillna(0).astype("float64")
    df["CostAmount"] = _parse_vn_decimal(df["Cost Amount"]).fillna(0).astype("float64")
    df["UnitPrice_float"] = _parse_vn_decimal(df["UnitPrice"])
    df["UnitCost_float"] = _parse_vn_decimal(df["Unit Cost"])
    df["is_return"] = (df["Quantity"] < 0) & (df["SalesAmount"] < 0) & (df["CostAmount"] < 0)
    df["sales_qty"] = df["Quantity"].clip(lower=0).astype("float64")
    df["return_qty"] = np.where(df["is_return"], -df["Quantity"], 0).astype("float64")
    df["net_qty"] = df["Quantity"].astype("float64")
    df["profit"] = (df["SalesAmount"] - df["CostAmount"]).astype("float64")
    return df[[
        "Date", "ItemCode", "Quantity", "sales_qty", "return_qty", "net_qty",
        "SalesAmount", "CostAmount", "profit", "UnitPrice_float", "UnitCost_float",
    ]].rename(columns={
        "SalesAmount": "sales_amount",
        "CostAmount": "cost_amount",
        "UnitPrice_float": "unit_price",
        "UnitCost_float": "unit_cost",
    })


def _find_forecast_csv(data_dir: Path) -> Path | None:
    """Find the best submission CSV in the artifacts directory."""
    artifact_dir = data_dir / "hbacc_best_model" / "data" / "artifacts"
    if not artifact_dir.exists():
        return None
    candidates = sorted(artifact_dir.glob("submission_*.csv"))
    return candidates[-1] if candidates else None


def _load_forecasts(path: Path, train_end: pd.Timestamp) -> pd.DataFrame:
    """Load wide submission CSV → long format with actual forecast dates."""
    df = pd.read_csv(path)
    value_cols = [c for c in df.columns if c.startswith("F")]

    rows = []
    for _, row in df.iterrows():
        id_str = str(row["id"])
        if "_validation" in id_str:
            sku = id_str.replace("_validation", "")
            day_offset_start = 1
        elif "_evaluation" in id_str:
            sku = id_str.replace("_evaluation", "")
            day_offset_start = 29
        else:
            continue

        for i, col in enumerate(value_cols):
            day_offset = day_offset_start + i
            forecast_date = train_end + pd.Timedelta(days=day_offset)
            rows.append({
                "sku": sku,
                "forecast_date": forecast_date,
                "day_offset": day_offset,
                "forecast_qty": max(0.0, float(row[col])),
            })

    return pd.DataFrame(rows)


def _make_sku_profile(daily: pd.DataFrame) -> pd.DataFrame:
    """Build per-SKU summary stats from daily sales."""
    g = daily.groupby("ItemCode")
    profile = pd.DataFrame({
        "total_sales_qty": g["sales_qty"].sum(),
        "total_sales_amount": g["sales_amount"].sum(),
        "total_profit": g["profit"].sum(),
        "active_days": g["sales_qty"].apply(lambda s: (s > 0).sum()),
        "total_days": g["Date"].count(),
        "avg_daily_qty": g["sales_qty"].mean(),
        "return_qty": g["return_qty"].sum(),
    })
    profit_sum = profile["total_profit"].clip(lower=0).sum()
    profile["profit_weight"] = (
        profile["total_profit"].clip(lower=0) / profit_sum if profit_sum > 0 else 0.0
    )
    profile["zero_ratio"] = 1.0 - profile["active_days"] / profile["total_days"].replace(0, np.nan)
    profile["zero_ratio"] = profile["zero_ratio"].fillna(1.0)
    profile["return_ratio"] = (
        profile["return_qty"] / profile["total_sales_qty"].replace(0, np.nan)
    ).fillna(0.0)
    return profile.reset_index()


class DuckDBClient:
    """
    In-process DuckDB database loaded with Dealight retail data.
    Provides the same interface as AthenaClient so all existing tool code works unchanged.
    """

    def __init__(self, data_dir: str, database_url: str | None = None) -> None:
        self._conn = duckdb.connect()
        self._database_url = database_url
        self._load_data(Path(data_dir))

    def _load_data(self, data_dir: Path) -> None:
        train_path = data_dir / "train.csv"
        _logger.info("Loading Dealight train data from %s", train_path)
        train = _load_train(train_path)
        _logger.info("Loaded %d transaction rows for %d SKUs", len(train), train["ItemCode"].nunique())

        # Register raw transactions as 'sales'
        self._conn.register("sales", train)

        # Build and register per-SKU profile
        profile = _make_sku_profile(train)
        self._conn.register("sku_profile", profile)

        # Determine train end date
        train_end = pd.Timestamp(train["Date"].max())
        _logger.info("Train data ends: %s", train_end.date())

        # Load forecasts — Postgres preferred (HBAAC native), fall back to submission CSV.
        forecasts: pd.DataFrame | None = None
        if self._database_url:
            forecasts = _load_forecasts_from_postgres(self._database_url)
            if not forecasts.empty:
                _logger.info("Loaded %d forecast rows from Postgres serving.sku_forecast", len(forecasts))
        if forecasts is None or forecasts.empty:
            forecast_path = _find_forecast_csv(data_dir)
            if forecast_path:
                _logger.info("Loading forecast from %s", forecast_path)
                forecasts = _load_forecasts(forecast_path, train_end)
                _logger.info("Loaded %d forecast rows from submission CSV", len(forecasts))
            else:
                _logger.warning("No forecast source available — forecasts table will be empty")
                forecasts = pd.DataFrame(columns=["sku", "forecast_date", "day_offset", "forecast_qty"])
        self._conn.register("forecasts", forecasts)

        self._train_end = train_end

    def refresh_forecasts(self) -> int:
        """Re-hydrate the forecasts table from Postgres (called after a successful retrain)."""
        if not self._database_url:
            return 0
        forecasts = _load_forecasts_from_postgres(self._database_url)
        self._conn.unregister("forecasts")
        self._conn.register("forecasts", forecasts)
        _logger.info("Refreshed forecasts table: %d rows", len(forecasts))
        return len(forecasts)

    @property
    def train_end(self) -> pd.Timestamp:
        return self._train_end

    def execute_query(self, sql: str) -> str:
        """Execute SQL and return results as a formatted string."""
        try:
            result = self._conn.execute(sql).df()
            if result.empty:
                return "Query returned no rows."
            return result.to_string(index=False, max_rows=50)
        except Exception as exc:
            return f"Query error: {exc}"

    def execute_query_as_records(self, sql: str) -> dict:
        """Execute SQL and return structured result dict (same shape as AthenaClient)."""
        try:
            result = self._conn.execute(sql).df()
            if result.empty:
                return {
                    "ok": False,
                    "status": "NO_DATA",
                    "reason": "Query executed successfully but returned no rows.",
                    "error_type": "NO_DATA",
                    "records": [],
                }
            # Convert timestamps/dates to ISO strings for JSON serialisation
            for col in result.columns:
                if hasattr(result[col], "dt") and hasattr(result[col].dt, "strftime"):
                    try:
                        result[col] = result[col].dt.strftime("%Y-%m-%d")
                    except Exception:
                        result[col] = result[col].astype(str)
            records = result.to_dict(orient="records")
            return {"ok": True, "records": records, "status": "SUCCEEDED"}
        except Exception as exc:
            error_str = str(exc)
            error_type = _classify_error(error_str)
            return {
                "ok": False,
                "status": "FAILED",
                "reason": error_str,
                "error_type": error_type,
                "records": [],
            }


def _classify_error(msg: str) -> str:
    msg_lower = msg.lower()
    if "syntax" in msg_lower:
        return "SYNTAX_ERROR"
    if "does not exist" in msg_lower or "not found" in msg_lower:
        return "TABLE_NOT_FOUND"
    if "column" in msg_lower and ("does not exist" in msg_lower or "unknown" in msg_lower):
        return "COLUMN_NOT_FOUND"
    return "QUERY_FAILED"
