from __future__ import annotations

import numpy as np
import pandas as pd


def rmsse_denominator(
    train_y: pd.DataFrame,
    floor: float = 1.0,
    eps: float = 1e-9,
) -> pd.Series:
    """RMSSE denominator: mean squared first-difference over the active period.

    M5-style: counts only periods after the SKU's first non-zero sale. SKUs with
    a flat active period (all diffs equal zero) or no active period fall back to
    `floor` so they cannot dominate the weighted score; `eps` is a final safety
    against division-by-zero.
    """
    values = train_y.to_numpy(dtype="float64", copy=False)
    n_rows, n_cols = values.shape
    if n_cols < 2:
        return pd.Series(
            np.full(n_rows, floor), index=train_y.index, name="rmsse_denom"
        )

    nonzero = values > 0
    has_any = nonzero.any(axis=1)
    first_nz = np.where(has_any, nonzero.argmax(axis=1), n_cols)

    diffs_sq = np.diff(values, axis=1) ** 2
    col_idx = np.arange(n_cols - 1)
    include = col_idx[None, :] >= first_nz[:, None]
    counts = include.sum(axis=1)
    sums = np.where(include, diffs_sq, 0.0).sum(axis=1)
    mean_diff_sq = np.where(counts > 0, sums / np.maximum(counts, 1), 0.0)
    denom = np.where(mean_diff_sq > 0, mean_diff_sq, floor)
    return pd.Series(np.maximum(denom, eps), index=train_y.index, name="rmsse_denom")


def wrmsse(
    actual: pd.DataFrame,
    forecast: pd.DataFrame,
    train_y: pd.DataFrame,
    weights: pd.Series,
    denom_floor: float = 1.0,
) -> tuple[float, pd.DataFrame]:
    actual, forecast = actual.align(forecast, join="left", axis=None, fill_value=0)
    denom = (
        rmsse_denominator(train_y, floor=denom_floor)
        .reindex(actual.index)
        .fillna(denom_floor)
    )
    weights = weights.reindex(actual.index).fillna(0).astype("float64")
    if weights.sum() > 0:
        weights = weights / weights.sum()

    mse = ((forecast - actual) ** 2).mean(axis=1)
    rmsse = np.sqrt(mse / denom)
    score_by_sku = pd.DataFrame(
        {
            "weight": weights,
            "denom": denom,
            "mse": mse,
            "rmsse": rmsse,
            "weighted_rmsse": weights * rmsse,
        }
    )
    return float(score_by_sku["weighted_rmsse"].sum()), score_by_sku
