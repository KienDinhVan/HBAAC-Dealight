from __future__ import annotations

import numpy as np
import pandas as pd

from hbacc_prj.metrics import wrmsse


def forecast_zero(train_y: pd.DataFrame, horizon_dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(0.0, index=train_y.index, columns=horizon_dates, dtype="float32")


def forecast_recent_mean(
    train_y: pd.DataFrame,
    horizon_dates: pd.DatetimeIndex,
    window: int = 28,
) -> pd.DataFrame:
    values = train_y.iloc[:, -window:].mean(axis=1).to_numpy(dtype="float32")
    out = np.repeat(values[:, None], len(horizon_dates), axis=1)
    return pd.DataFrame(out, index=train_y.index, columns=horizon_dates)


def forecast_recent_median(
    train_y: pd.DataFrame,
    horizon_dates: pd.DatetimeIndex,
    window: int = 56,
) -> pd.DataFrame:
    values = train_y.iloc[:, -window:].median(axis=1).to_numpy(dtype="float32")
    out = np.repeat(values[:, None], len(horizon_dates), axis=1)
    return pd.DataFrame(out, index=train_y.index, columns=horizon_dates)


def forecast_same_weekday_mean(
    train_y: pd.DataFrame,
    horizon_dates: pd.DatetimeIndex,
    weeks: int = 8,
) -> pd.DataFrame:
    train_dates = pd.DatetimeIndex(train_y.columns)
    parts = []
    for target_date in horizon_dates:
        candidate_dates = [
            target_date - pd.Timedelta(days=7 * lag) for lag in range(1, weeks + 1)
        ]
        candidate_dates = [d for d in candidate_dates if d in train_dates]
        if candidate_dates:
            values = train_y.loc[:, candidate_dates].mean(axis=1)
        else:
            values = pd.Series(0.0, index=train_y.index)
        parts.append(values.astype("float32"))
    return pd.concat(parts, axis=1).set_axis(horizon_dates, axis=1)


def forecast_blend(
    forecasts: dict[str, pd.DataFrame],
    weights: dict[str, float],
) -> pd.DataFrame:
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("Blend weights must sum to a positive value.")
    out = None
    for name, forecast in forecasts.items():
        weight = weights.get(name, 0.0) / total
        out = forecast * weight if out is None else out + forecast * weight
    return out.clip(lower=0).astype("float32")


def forecast_conservative_sparse(
    train_y: pd.DataFrame,
    horizon_dates: pd.DatetimeIndex,
    window: int = 56,
) -> pd.DataFrame:
    base = forecast_recent_median(train_y, horizon_dates, window)
    values = train_y.to_numpy()
    active_days = (values > 0).sum(axis=1)
    last_seen = np.full(values.shape[0], 9999, dtype="int32")
    for row_idx, row in enumerate(values):
        pos = np.flatnonzero(row > 0)
        if len(pos):
            last_seen[row_idx] = values.shape[1] - 1 - pos[-1]

    factor = np.ones(values.shape[0], dtype="float32")
    factor[active_days <= 2] *= 0.15
    factor[(active_days > 2) & (active_days <= 5)] *= 0.35
    factor[(active_days > 5) & (active_days <= 15)] *= 0.65
    factor[last_seen > 180] *= 0.05
    factor[(last_seen > 90) & (last_seen <= 180)] *= 0.35
    factor[(last_seen > 56) & (last_seen <= 90)] *= 0.70
    return base.mul(factor, axis=0).clip(lower=0).astype("float32")


def evaluate_baselines(
    y: pd.DataFrame,
    sku_weights: pd.Series,
    daily: pd.DataFrame | None = None,
    horizon: int = 56,
    step: int = 28,
    n_folds: int = 3,
) -> pd.DataFrame:
    last_date = pd.DatetimeIndex(y.columns).max()
    rows = []
    for fold_idx in range(n_folds):
        train_end = last_date - pd.Timedelta(days=horizon + step * (n_folds - 1 - fold_idx))
        valid_dates = pd.date_range(train_end + pd.Timedelta(days=1), periods=horizon, freq="D")
        train_y = y.loc[:, y.columns <= train_end]
        actual = y.loc[:, valid_dates]
        weights = sku_weights
        if daily is not None:
            from hbacc_prj.data import make_sku_profile_from_daily

            weights = make_sku_profile_from_daily(daily, y, as_of=train_end)["profit_weight"]
        forecasts = {
            "zero": forecast_zero(train_y, valid_dates),
            "mean_28": forecast_recent_mean(train_y, valid_dates, 28),
            "mean_56": forecast_recent_mean(train_y, valid_dates, 56),
            "median_56": forecast_recent_median(train_y, valid_dates, 56),
            "same_weekday_8w": forecast_same_weekday_mean(train_y, valid_dates, 8),
            "conservative_sparse": forecast_conservative_sparse(train_y, valid_dates, 56),
        }
        forecasts["blend_mean_weekday"] = forecast_blend(
            forecasts, {"mean_28": 0.45, "mean_56": 0.25, "same_weekday_8w": 0.30}
        )
        for name, forecast in forecasts.items():
            score_56, _ = wrmsse(actual, forecast, train_y, weights)
            score_28, _ = wrmsse(actual.iloc[:, :28], forecast.iloc[:, :28], train_y, weights)
            rows.append(
                {
                    "fold": fold_idx + 1,
                    "train_end": train_end.date().isoformat(),
                    "valid_start": valid_dates.min().date().isoformat(),
                    "valid_end": valid_dates.max().date().isoformat(),
                    "model": name,
                    "wrmsse_28": score_28,
                    "wrmsse_56": score_56,
                }
            )
    return pd.DataFrame(rows)
