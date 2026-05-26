from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import psycopg

from hbacc_prj.data import parse_vn_decimal

REQUIRED_RAW_COLUMNS = [
    "Date",
    "Stt",
    "ItemCode",
    "Quantity",
    "UnitPrice",
    "SalesAmount",
    "Unit Cost",
    "Cost Amount",
]
Stage = Literal["validate", "raw", "bronze", "silver", "gold", "quality", "all"]


def parse_decimal_comma(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    parsed = parse_vn_decimal(pd.Series([value])).iloc[0]
    return None if pd.isna(parsed) else float(parsed)


def parse_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(str(value).strip(), format="%Y-%m-%d", errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def normalize_item_code(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    normalized = re.sub(r"\s+", "", str(value).strip().upper())
    return normalized or None


def split_sales_return(
    quantity: float | int | None,
) -> tuple[float | None, float | None]:
    if quantity is None or pd.isna(quantity):
        return None, None
    quantity_value = float(quantity)
    return max(quantity_value, 0.0), max(-quantity_value, 0.0)


def validate_raw_file(source_path: Path) -> list[str]:
    if not source_path.exists():
        raise FileNotFoundError(f"Raw source file does not exist: {source_path}")
    columns = pd.read_csv(source_path, nrows=0).columns.tolist()
    if columns != REQUIRED_RAW_COLUMNS:
        raise ValueError(f"Unexpected raw columns: {columns}")
    return columns


def load_raw_frame(source_path: Path, batch_id: str) -> pd.DataFrame:
    validate_raw_file(source_path)
    source = pd.read_csv(source_path, dtype="string", keep_default_na=False)
    source = source.rename(
        columns={
            "Date": "date_raw",
            "Stt": "stt_raw",
            "ItemCode": "item_code_raw",
            "Quantity": "quantity_raw",
            "UnitPrice": "unit_price_raw",
            "SalesAmount": "sales_amount_raw",
            "Unit Cost": "unit_cost_raw",
            "Cost Amount": "cost_amount_raw",
        }
    )
    source.insert(0, "source_row_number", range(1, len(source) + 1))
    source.insert(0, "source_file", source_path.name)
    source.insert(0, "batch_id", batch_id)
    source["ingested_at"] = pd.Timestamp.now(tz="UTC")
    return source


def build_bronze(raw_frame: pd.DataFrame) -> pd.DataFrame:
    bronze = raw_frame[
        [
            "batch_id",
            "source_file",
            "source_row_number",
            "stt_raw",
            "date_raw",
            "item_code_raw",
            "quantity_raw",
            "unit_price_raw",
            "sales_amount_raw",
            "unit_cost_raw",
            "cost_amount_raw",
            "ingested_at",
        ]
    ].copy()
    bronze["stt"] = pd.to_numeric(bronze.pop("stt_raw"), errors="coerce").astype(
        "Int64"
    )
    bronze["quantity"] = parse_vn_decimal(bronze.pop("quantity_raw"))
    bronze["sales_amount"] = parse_vn_decimal(bronze.pop("sales_amount_raw"))
    bronze["cost_amount"] = parse_vn_decimal(bronze.pop("cost_amount_raw"))
    bronze["transformed_at"] = pd.Timestamp.now(tz="UTC")
    return bronze[
        [
            "batch_id",
            "source_file",
            "source_row_number",
            "stt",
            "date_raw",
            "item_code_raw",
            "quantity",
            "unit_price_raw",
            "sales_amount",
            "unit_cost_raw",
            "cost_amount",
            "ingested_at",
            "transformed_at",
        ]
    ]


def _append_error(reasons: pd.Series, mask: pd.Series, label: str) -> pd.Series:
    existing = reasons.fillna("")
    appended = existing.mask(existing.eq(""), label).mask(
        existing.ne(""), existing + ";" + label
    )
    return appended.where(mask, reasons).astype("string")


def build_silver(bronze_frame: pd.DataFrame) -> pd.DataFrame:
    silver = bronze_frame[
        [
            "batch_id",
            "source_row_number",
            "date_raw",
            "item_code_raw",
            "quantity",
            "unit_price_raw",
            "sales_amount",
            "unit_cost_raw",
            "cost_amount",
        ]
    ].copy()
    silver["date"] = pd.to_datetime(
        silver.pop("date_raw"), format="%Y-%m-%d", errors="coerce"
    ).dt.date
    silver["item_code"] = (
        silver.pop("item_code_raw")
        .astype("string")
        .str.strip()
        .str.upper()
        .str.replace(r"\s+", "", regex=True)
        .replace("", pd.NA)
    )
    silver["unit_price"] = parse_vn_decimal(silver.pop("unit_price_raw"))
    silver["unit_cost"] = parse_vn_decimal(silver.pop("unit_cost_raw"))
    silver["sales_quantity"] = silver["quantity"].clip(lower=0)
    silver["return_quantity"] = (-silver["quantity"]).clip(lower=0)
    silver["is_return"] = silver["quantity"].lt(0).fillna(False)

    errors = pd.Series(pd.NA, index=silver.index, dtype="string")
    error_rules = [
        (silver["date"].isna(), "invalid_date"),
        (silver["item_code"].isna(), "missing_item_code"),
        (silver["quantity"].isna(), "invalid_quantity"),
        (silver["unit_price"].isna(), "invalid_unit_price"),
        (silver["sales_amount"].isna(), "invalid_sales_amount"),
        (silver["unit_cost"].isna(), "invalid_unit_cost"),
        (silver["cost_amount"].isna(), "invalid_cost_amount"),
    ]
    for mask, label in error_rules:
        errors = _append_error(errors, mask, label)
    silver["error_reason"] = errors
    silver["is_valid"] = errors.isna()
    silver["transformed_at"] = pd.Timestamp.now(tz="UTC")
    return silver[
        [
            "batch_id",
            "source_row_number",
            "date",
            "item_code",
            "quantity",
            "sales_quantity",
            "return_quantity",
            "unit_price",
            "sales_amount",
            "unit_cost",
            "cost_amount",
            "is_return",
            "is_valid",
            "error_reason",
            "transformed_at",
        ]
    ]


def aggregate_daily_sales(silver_frame: pd.DataFrame) -> pd.DataFrame:
    valid = silver_frame.loc[silver_frame["is_valid"]].copy()
    gold = (
        valid.groupby(["date", "item_code"], as_index=False, observed=True)
        .agg(
            quantity_sold=("sales_quantity", "sum"),
            return_quantity=("return_quantity", "sum"),
            net_quantity=("quantity", "sum"),
            sales_amount=("sales_amount", "sum"),
            cost_amount=("cost_amount", "sum"),
            avg_unit_price=("unit_price", "mean"),
            avg_unit_cost=("unit_cost", "mean"),
            transaction_count=("quantity", "size"),
        )
        .sort_values(["date", "item_code"])
        .reset_index(drop=True)
    )
    gold["created_at"] = pd.Timestamp.now(tz="UTC")
    return gold


def evaluate_quality(
    silver_frame: pd.DataFrame, gold_frame: pd.DataFrame
) -> dict[str, int]:
    failed_rules = {
        "invalid_silver_rows": int((~silver_frame["is_valid"]).sum()),
        "null_gold_dates": int(gold_frame["date"].isna().sum()),
        "null_gold_item_codes": int(gold_frame["item_code"].isna().sum()),
        "duplicate_gold_keys": int(gold_frame.duplicated(["date", "item_code"]).sum()),
        "negative_return_quantity": int((gold_frame["return_quantity"] < 0).sum()),
        "invalid_transaction_count": int((gold_frame["transaction_count"] < 1).sum()),
    }
    errors = {rule: count for rule, count in failed_rules.items() if count}
    if errors:
        raise ValueError(f"Data quality checks failed: {errors}")
    return {
        "silver_rows": len(silver_frame),
        "valid_silver_rows": int(silver_frame["is_valid"].sum()),
        "gold_rows": len(gold_frame),
        **failed_rules,
    }


def apply_pipeline_schema(
    connection: psycopg.Connection[Any], schema_path: Path
) -> None:
    connection.execute(schema_path.read_text(encoding="utf-8"))


def _to_database_value(value: object) -> object:
    return None if pd.isna(value) else value


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
            copy.write_row(tuple(_to_database_value(value) for value in row))


def _read_batch(
    connection: psycopg.Connection[Any], table_name: str, batch_id: str
) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT * FROM {table_name} WHERE batch_id = %s ORDER BY source_row_number",
            (batch_id,),
        )
        columns = [description.name for description in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def ingest_raw(
    connection: psycopg.Connection[Any], source_path: Path, batch_id: str
) -> pd.DataFrame:
    raw = load_raw_frame(source_path, batch_id)
    connection.execute("DELETE FROM raw.transactions WHERE batch_id = %s", (batch_id,))
    _copy_frame(
        connection,
        "raw.transactions",
        [
            "batch_id",
            "source_file",
            "source_row_number",
            "date_raw",
            "stt_raw",
            "item_code_raw",
            "quantity_raw",
            "unit_price_raw",
            "sales_amount_raw",
            "unit_cost_raw",
            "cost_amount_raw",
            "ingested_at",
        ],
        raw,
    )
    return raw


def persist_bronze(
    connection: psycopg.Connection[Any],
    batch_id: str,
    raw_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    raw = (
        raw_frame
        if raw_frame is not None
        else _read_batch(connection, "raw.transactions", batch_id)
    )
    bronze = build_bronze(raw)
    connection.execute(
        "DELETE FROM bronze.transactions WHERE batch_id = %s", (batch_id,)
    )
    _copy_frame(connection, "bronze.transactions", list(bronze.columns), bronze)
    return bronze


def persist_silver(
    connection: psycopg.Connection[Any],
    batch_id: str,
    bronze_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    bronze = (
        bronze_frame
        if bronze_frame is not None
        else _read_batch(connection, "bronze.transactions", batch_id)
    )
    silver = build_silver(bronze)
    connection.execute(
        "DELETE FROM silver.transactions_clean WHERE batch_id = %s", (batch_id,)
    )
    _copy_frame(connection, "silver.transactions_clean", list(silver.columns), silver)
    return silver


def persist_gold(
    connection: psycopg.Connection[Any],
    batch_id: str,
    silver_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    silver = (
        silver_frame
        if silver_frame is not None
        else _read_batch(connection, "silver.transactions_clean", batch_id)
    )
    gold = aggregate_daily_sales(silver)
    connection.execute("DELETE FROM gold.daily_sku_sales")
    _copy_frame(connection, "gold.daily_sku_sales", list(gold.columns), gold)
    return gold


def read_gold(connection: psycopg.Connection[Any]) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM gold.daily_sku_sales ORDER BY date, item_code")
        columns = [description.name for description in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def run_pipeline_stage(
    database_url: str,
    source_path: Path,
    batch_id: str,
    schema_path: Path,
    stage: Stage = "all",
) -> dict[str, int]:
    if stage == "validate":
        validate_raw_file(source_path)
        return {"validated": 1}
    with psycopg.connect(database_url) as connection:
        apply_pipeline_schema(connection, schema_path)
        if stage == "raw":
            return {"raw_rows": len(ingest_raw(connection, source_path, batch_id))}
        if stage == "bronze":
            return {"bronze_rows": len(persist_bronze(connection, batch_id))}
        if stage == "silver":
            return {"silver_rows": len(persist_silver(connection, batch_id))}
        if stage == "gold":
            return {"gold_rows": len(persist_gold(connection, batch_id))}
        if stage == "quality":
            silver = _read_batch(connection, "silver.transactions_clean", batch_id)
            return evaluate_quality(silver, read_gold(connection))

        raw = ingest_raw(connection, source_path, batch_id)
        bronze = persist_bronze(connection, batch_id, raw)
        silver = persist_silver(connection, batch_id, bronze)
        gold = persist_gold(connection, batch_id, silver)
        summary = evaluate_quality(silver, gold)
        summary.update(
            {
                "raw_rows": len(raw),
                "bronze_rows": len(bronze),
            }
        )
        return summary
