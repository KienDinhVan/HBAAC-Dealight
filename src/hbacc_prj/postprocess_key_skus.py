from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from hbacc_prj.model_twostage import VALUE_COLUMNS


def _weekday_reference(
    train_y: pd.DataFrame,
    sku: str,
    horizon_dates: pd.DatetimeIndex,
    n: int,
    stat: str,
    month_factor: bool,
    trend: bool,
) -> np.ndarray:
    hist = train_y.loc[sku]
    recent56 = hist.iloc[-56:].mean()
    prev56 = hist.iloc[-112:-56].mean() if len(hist) >= 112 else recent56
    trend_factor = (recent56 + 1.0) / (prev56 + 1.0) if trend else 1.0

    since_2022 = hist.loc[hist.index >= pd.Timestamp("2022-01-01")]
    all_non_sunday = since_2022.loc[since_2022.index.dayofweek != 6]
    values = []
    for target_date in horizon_dates:
        if target_date.dayofweek == 6:
            values.append(0.0)
            continue
        same_dow = hist.loc[hist.index.dayofweek == target_date.dayofweek].tail(n)
        base = same_dow.mean() if stat == "mean" else same_dow.median()
        if month_factor:
            same_month = since_2022.loc[
                (since_2022.index.month == target_date.month)
                & (since_2022.index.dayofweek != 6)
            ]
            factor = (
                (same_month.mean() + 1.0) / (all_non_sunday.mean() + 1.0)
                if len(same_month)
                else 1.0
            )
            base *= factor
        values.append(max(float(base * trend_factor), 0.0))
    return np.asarray(values, dtype="float32")


def _submission_values(submission: pd.DataFrame, sku: str) -> np.ndarray:
    parts = []
    for suffix in ["validation", "evaluation"]:
        row = submission.loc[submission["id"].eq(f"{sku}_{suffix}")]
        if len(row) != 1:
            raise ValueError(f"Missing or duplicate row for {sku}_{suffix}")
        parts.append(row[VALUE_COLUMNS].iloc[0].to_numpy(dtype="float32"))
    return np.concatenate(parts)


def _set_submission_values(submission: pd.DataFrame, sku: str, values: np.ndarray) -> None:
    if len(values) != 56:
        raise ValueError("Expected 56 forecast values")
    for suffix, part in [
        ("validation", values[:28]),
        ("evaluation", values[28:]),
    ]:
        mask = submission["id"].eq(f"{sku}_{suffix}")
        if mask.sum() != 1:
            raise ValueError(f"Missing or duplicate row for {sku}_{suffix}")
        submission.loc[mask, VALUE_COLUMNS] = part.astype("float32")


def adjust_submission(
    input_path: Path,
    output_path: Path,
    train_end: str,
    sku2_alpha: float,
    sku3_alpha: float,
) -> None:
    y = pd.read_pickle("data/processed/daily_demand_matrix.pkl")
    train_end_ts = pd.Timestamp(train_end)
    train_y = y.loc[:, pd.DatetimeIndex(y.columns) <= train_end_ts]
    horizon_dates = pd.date_range(train_end_ts + pd.Timedelta(days=1), periods=56, freq="D")
    submission = pd.read_csv(input_path)

    rules = {
        "SKU-00002": {
            "alpha": sku2_alpha,
            "n": 26,
            "stat": "median",
            "month_factor": True,
            "trend": True,
        },
        "SKU-00003": {
            "alpha": sku3_alpha,
            "n": 26,
            "stat": "mean",
            "month_factor": True,
            "trend": True,
        },
    }
    for sku, rule in rules.items():
        alpha = float(rule.pop("alpha"))
        if alpha <= 0:
            continue
        current = _submission_values(submission, sku)
        reference = _weekday_reference(train_y, sku, horizon_dates, **rule)
        blended = alpha * reference + (1 - alpha) * current
        _set_submission_values(submission, sku, np.clip(blended, 0, None))

    if submission[VALUE_COLUMNS].isna().any().any():
        raise ValueError("adjusted submission has missing values")
    submission[VALUE_COLUMNS] = submission[VALUE_COLUMNS].clip(lower=0).astype("float32")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    submission.to_csv(output_path, index=False)
    print(
        f"{output_path.name}: rows={len(submission):,}, "
        f"sum={float(submission[VALUE_COLUMNS].sum().sum()):.2f}, "
        f"min={float(submission[VALUE_COLUMNS].min().min()):.6f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--train-end", default="2025-09-05")
    parser.add_argument("--sku2-alpha", type=float, default=0.0)
    parser.add_argument("--sku3-alpha", type=float, default=0.15)
    args = parser.parse_args()
    adjust_submission(args.input, args.output, args.train_end, args.sku2_alpha, args.sku3_alpha)


if __name__ == "__main__":
    main()
