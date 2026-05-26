from datetime import date

import pandas as pd

from hbacc_prj.features import build_training_frame, validate_features


def _gold_frame() -> pd.DataFrame:
    days = pd.date_range("2025-01-01", periods=70, freq="D")
    rows = []
    for sku in ["SKU-A", "SKU-B"]:
        for index, day in enumerate(days):
            rows.append(
                {
                    "date": day.date(),
                    "item_code": sku,
                    "quantity_sold": float(index % 5),
                    "return_quantity": 1.0 if index == 5 else 0.0,
                    "avg_unit_price": 10.0 + index,
                }
            )
    return pd.DataFrame(rows)


def test_training_frame_has_all_horizons_and_future_targets() -> None:
    frame = build_training_frame(
        _gold_frame(), "test-v1", max_skus=2, as_of_days=3, max_horizon=7
    )

    summary = validate_features(frame)

    assert summary["rows"] == 2 * 3 * 7
    assert set(frame["horizon"]) == set(range(1, 8))
    assert (
        pd.to_datetime(frame["target_date"]) > pd.to_datetime(frame["as_of_date"])
    ).all()
    sample = frame.loc[
        (frame["item_code"] == "SKU-A")
        & (frame["as_of_date"] == date(2025, 3, 4))
        & (frame["horizon"] == 1)
    ].iloc[0]
    assert sample["target_date"] == date(2025, 3, 5)
    assert sample["target_quantity"] == 3.0


def test_lag_and_rolling_features_do_not_use_target_day() -> None:
    gold = _gold_frame()
    gold.loc[
        (gold["item_code"] == "SKU-A") & (gold["date"] == date(2025, 3, 5)),
        "quantity_sold",
    ] = 10000.0
    frame = build_training_frame(
        gold, "test-v1", max_skus=2, as_of_days=3, max_horizon=7
    )
    target_row = frame.loc[
        (frame["item_code"] == "SKU-A")
        & (frame["as_of_date"] == date(2025, 3, 4))
        & (frame["horizon"] == 1)
    ].iloc[0]

    assert target_row["target_quantity"] == 10000.0
    assert target_row["lag_1"] == 1.0
    assert target_row["rolling_max_28"] < 10000.0
