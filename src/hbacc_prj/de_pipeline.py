"""Pure processing + data-quality logic for the DE GCS pipeline (DE_arch.png).

No GCS/BigQuery IO here — IO lives in scripts/run_de_pipeline.py so these
functions stay unit-testable with plain DataFrames.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

REQUIRED_COLUMNS: tuple[str, ...] = (
    "Date",
    "Stt",
    "ItemCode",
    "Quantity",
    "UnitPrice",
    "SalesAmount",
    "Unit Cost",
    "Cost Amount",
)

_STAGING_RENAME = {
    "Date": "date",
    "Stt": "stt",
    "ItemCode": "item_code",
    "Quantity": "quantity",
    "UnitPrice": "unit_price",
    "SalesAmount": "sales_amount",
    "Unit Cost": "unit_cost",
    "Cost Amount": "cost_amount",
}


class SchemaValidationError(ValueError):
    """Raised when the uploaded CSV is missing required columns."""


@dataclass(frozen=True)
class ValidationResult:
    passed: pd.DataFrame
    rejected: pd.DataFrame


def validate_transactions(df: pd.DataFrame) -> ValidationResult:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise SchemaValidationError(f"Missing required columns: {missing}")

    parsed_date = pd.to_datetime(df["Date"], errors="coerce")
    quantity = pd.to_numeric(df["Quantity"], errors="coerce")
    unit_price = pd.to_numeric(df["UnitPrice"], errors="coerce")
    item_code = df["ItemCode"].astype("string").str.strip()

    checks = [
        ("invalid_date", parsed_date.isna()),
        ("missing_item_code", item_code.isna() | (item_code == "")),
        ("invalid_quantity", quantity.isna() | (quantity < 0)),
        ("negative_unit_price", unit_price < 0),
    ]
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
    for reason, mask in checks:
        for idx in df.index[mask.fillna(False)]:
            reasons.loc[idx].append(reason)

    bad_mask = reasons.map(len) > 0
    rejected = df.loc[bad_mask].copy()
    rejected["reject_reason"] = reasons.loc[bad_mask].map(";".join)

    passed = df.loc[~bad_mask, list(REQUIRED_COLUMNS)].rename(columns=_STAGING_RENAME)
    passed = passed.assign(
        date=parsed_date.loc[~bad_mask],
        item_code=item_code.loc[~bad_mask],
        quantity=quantity.loc[~bad_mask],
        unit_price=unit_price.loc[~bad_mask],
        sales_amount=pd.to_numeric(passed["sales_amount"], errors="coerce"),
        unit_cost=pd.to_numeric(passed["unit_cost"], errors="coerce"),
        cost_amount=pd.to_numeric(passed["cost_amount"], errors="coerce"),
    ).reset_index(drop=True)
    return ValidationResult(passed=passed, rejected=rejected.reset_index(drop=True))


def build_curated(staging_df: pd.DataFrame, batch_id: str) -> pd.DataFrame:
    curated = (
        staging_df.assign(date=staging_df["date"].dt.date)
        .groupby(["date", "item_code"], as_index=False)
        .agg(
            total_quantity=("quantity", "sum"),
            total_sales=("sales_amount", "sum"),
            total_cost=("cost_amount", "sum"),
            txn_count=("quantity", "size"),
        )
        .sort_values(["date", "item_code"])
        .reset_index(drop=True)
    )
    curated = curated.astype(
        {
            "total_quantity": "float64",
            "total_sales": "float64",
            "total_cost": "float64",
            "txn_count": "int64",
        }
    )
    curated["batch_id"] = batch_id
    curated["loaded_at"] = pd.Timestamp.now(tz="UTC")
    return curated
