"""Tests for DE pipeline pure logic (DE_arch: processing + DQ validation)."""
from __future__ import annotations

import pandas as pd
import pytest

from hbacc_prj.de_pipeline import (
    SchemaValidationError,
    build_curated,
    validate_transactions,
)


def _make_df(**overrides) -> pd.DataFrame:
    base = {
        "Date": ["2025-01-02", "2025-01-02", "2025-01-03"],
        "Stt": [1, 2, 3],
        "ItemCode": ["SKU-1", "SKU-1", "SKU-2"],
        "Quantity": [2, 3, 1],
        "UnitPrice": [10.0, 10.0, 5.0],
        "SalesAmount": [20.0, 30.0, 5.0],
        "Unit Cost": [4.0, 4.0, 2.0],
        "Cost Amount": [8.0, 12.0, 2.0],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_missing_column_raises_schema_error() -> None:
    df = _make_df().drop(columns=["ItemCode"])
    with pytest.raises(SchemaValidationError, match="ItemCode"):
        validate_transactions(df)


def test_all_valid_rows_pass_with_typed_snake_case_columns() -> None:
    result = validate_transactions(_make_df())
    assert len(result.passed) == 3
    assert len(result.rejected) == 0
    assert list(result.passed.columns) == [
        "date", "stt", "item_code", "quantity",
        "unit_price", "sales_amount", "unit_cost", "cost_amount",
    ]
    assert str(result.passed["date"].dtype) == "datetime64[ns]"
    assert result.passed["quantity"].tolist() == [2.0, 3.0, 1.0]


def test_bad_rows_are_rejected_with_reasons() -> None:
    df = _make_df(
        Date=["not-a-date", "2025-01-02", "2025-01-03"],
        ItemCode=["SKU-1", "", "SKU-2"],
        Quantity=[2, 3, -1],
    )
    result = validate_transactions(df)
    assert len(result.passed) == 0
    assert len(result.rejected) == 3
    reasons = result.rejected["reject_reason"].tolist()
    assert reasons == ["invalid_date", "missing_item_code", "invalid_quantity"]


def test_multiple_defects_join_reasons_with_semicolon() -> None:
    df = _make_df(
        Date=["not-a-date", "2025-01-02", "2025-01-03"],
        Quantity=[-5, 3, 1],
    )
    result = validate_transactions(df)
    assert result.rejected.iloc[0]["reject_reason"] == "invalid_date;invalid_quantity"
    assert len(result.passed) == 2


def test_negative_unit_price_rejected() -> None:
    df = _make_df(UnitPrice=[10.0, -1.0, 5.0])
    result = validate_transactions(df)
    assert len(result.rejected) == 1
    assert result.rejected.iloc[0]["reject_reason"] == "negative_unit_price"


def test_build_curated_aggregates_by_date_and_item() -> None:
    staging = validate_transactions(_make_df()).passed
    curated = build_curated(staging, batch_id="batch-1")
    assert list(curated.columns) == [
        "date", "item_code", "total_quantity", "total_sales",
        "total_cost", "txn_count", "batch_id", "loaded_at",
    ]
    # SKU-1 has two txns on 2025-01-02 -> aggregated to one row
    assert len(curated) == 2
    sku1 = curated[curated["item_code"] == "SKU-1"].iloc[0]
    assert sku1["total_quantity"] == 5.0
    assert sku1["total_sales"] == 50.0
    assert sku1["total_cost"] == 20.0
    assert sku1["txn_count"] == 2
    assert (curated["batch_id"] == "batch-1").all()
