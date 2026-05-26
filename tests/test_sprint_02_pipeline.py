from datetime import date

import pandas as pd
import pytest

from hbacc_prj.pipeline import (
    aggregate_daily_sales,
    build_bronze,
    build_silver,
    evaluate_quality,
    load_raw_frame,
    normalize_item_code,
    parse_date,
    parse_decimal_comma,
    split_sales_return,
)


def _write_source(tmp_path, rows: list[list[object]]):
    path = tmp_path / "train.csv"
    pd.DataFrame(
        rows,
        columns=[
            "Date",
            "Stt",
            "ItemCode",
            "Quantity",
            "UnitPrice",
            "SalesAmount",
            "Unit Cost",
            "Cost Amount",
        ],
    ).to_csv(path, index=False)
    return path


def test_cleaning_helpers_cover_decimal_date_item_and_return() -> None:
    assert parse_decimal_comma("12,5") == 12.5
    assert parse_decimal_comma("1.234,5") == 1234.5
    assert parse_date("2025-09-05") == date(2025, 9, 5)
    assert normalize_item_code(" sku- 01 ") == "SKU-01"
    assert split_sales_return(-3) == (0.0, 3.0)


def test_pipeline_transforms_and_aggregates_daily_sales(tmp_path) -> None:
    source = _write_source(
        tmp_path,
        [
            ["2025-01-01", 1, "sku-01", 5, "12,5", 62.5, "5,0", 25],
            ["2025-01-01", 2, "SKU-01", -2, "12,5", -25, "5,0", -10],
            ["2025-01-02", 3, "SKU-02", 1, 10, 10, 4, 4],
        ],
    )

    raw = load_raw_frame(source, "batch-1")
    bronze = build_bronze(raw)
    silver = build_silver(bronze)
    gold = aggregate_daily_sales(silver)

    summary = evaluate_quality(silver, gold)
    sku_day = gold.loc[
        (gold["date"] == date(2025, 1, 1)) & (gold["item_code"] == "SKU-01")
    ].iloc[0]
    assert len(raw) == len(bronze) == len(silver) == 3
    assert summary["gold_rows"] == 2
    assert sku_day["quantity_sold"] == 5
    assert sku_day["return_quantity"] == 2
    assert sku_day["net_quantity"] == 3
    assert sku_day["transaction_count"] == 2
    assert not gold.duplicated(["date", "item_code"]).any()


def test_silver_marks_invalid_rows_and_quality_gate_fails(tmp_path) -> None:
    source = _write_source(
        tmp_path,
        [["bad-date", 1, "", 1, "not-a-number", 10, "5,0", 5]],
    )
    silver = build_silver(build_bronze(load_raw_frame(source, "bad-batch")))
    gold = aggregate_daily_sales(silver)

    assert silver.loc[0, "is_valid"] == False  # noqa: E712
    assert "invalid_date" in silver.loc[0, "error_reason"]
    assert "missing_item_code" in silver.loc[0, "error_reason"]
    assert "invalid_unit_price" in silver.loc[0, "error_reason"]
    with pytest.raises(ValueError, match="invalid_silver_rows"):
        evaluate_quality(silver, gold)


def test_rebuilding_gold_from_same_batch_is_stable(tmp_path) -> None:
    source = _write_source(
        tmp_path,
        [["2025-01-01", 1, "SKU-01", 2, 10, 20, 4, 8]],
    )
    silver = build_silver(build_bronze(load_raw_frame(source, "repeatable")))

    first = aggregate_daily_sales(silver).drop(columns=["created_at"])
    second = aggregate_daily_sales(silver).drop(columns=["created_at"])

    pd.testing.assert_frame_equal(first, second)
