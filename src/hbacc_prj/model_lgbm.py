from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from hbacc_prj.baselines import forecast_recent_median
from hbacc_prj.data import make_sku_profile_from_daily
from hbacc_prj.metrics import rmsse_denominator, wrmsse
from hbacc_prj.segments import select_skus


@dataclass(frozen=True)
class LGBMConfig:
    top_n_skus: int = 1000
    horizon: int = 56
    origin_lookback_days: int = 365
    origin_stride: int = 14
    valid_train_end: str = "2025-07-11"
    num_boost_round: int = 350
    early_stopping_rounds: int = 40
    random_seed: int = 2026
    sku_strategy: str = "top_profit"
    min_active_days: int = 50
    max_days_since_last_sale: int = 56
    filter_inactive: bool = False
    time_consistent_profile: bool = True

    @property
    def run_name(self) -> str:
        suffix = "_fi" if self.filter_inactive else ""
        profile_suffix = "_tc" if self.time_consistent_profile else ""
        return (
            f"{self.sku_strategy}_top{self.top_n_skus}"
            f"_a{self.min_active_days}_r{self.max_days_since_last_sale}"
            f"_lb{self.origin_lookback_days}"
            f"_s{self.origin_stride}{suffix}{profile_suffix}_{self.valid_train_end}"
        )


def _top_skus(profile: pd.DataFrame, top_n: int) -> pd.Index:
    return profile.sort_values("profit_weight", ascending=False).head(top_n).index


def selected_skus(profile: pd.DataFrame, cfg: LGBMConfig) -> pd.Index:
    return select_skus(
        profile,
        strategy=cfg.sku_strategy,
        top_n=cfg.top_n_skus,
        min_active_days=cfg.min_active_days,
        max_days_since_last_sale=cfg.max_days_since_last_sale,
    )


def load_profile_for_date(y: pd.DataFrame, as_of: pd.Timestamp, cfg: LGBMConfig) -> pd.DataFrame:
    if not cfg.time_consistent_profile:
        return pd.read_pickle("data/artifacts/sku_profile.pkl")

    daily = pd.read_pickle("data/processed/daily_sales.pkl")
    return make_sku_profile_from_daily(daily, y, as_of=as_of)


def _origin_features(
    values: np.ndarray,
    dates: pd.DatetimeIndex,
    origin_pos: int,
    feature_date: pd.Timestamp | None = None,
) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for lag in [1, 7, 14, 28, 56, 84, 112, 168, 364]:
        if origin_pos - lag >= 0:
            out[f"lag_{lag}"] = values[:, origin_pos - lag]
        else:
            out[f"lag_{lag}"] = np.zeros(values.shape[0], dtype="float32")

    for window in [7, 14, 28, 56, 112]:
        start = max(0, origin_pos - window)
        hist = values[:, start:origin_pos]
        if hist.shape[1] == 0:
            out[f"mean_{window}"] = np.zeros(values.shape[0], dtype="float32")
            out[f"max_{window}"] = np.zeros(values.shape[0], dtype="float32")
            out[f"active_{window}"] = np.zeros(values.shape[0], dtype="float32")
        else:
            out[f"mean_{window}"] = hist.mean(axis=1)
            out[f"max_{window}"] = hist.max(axis=1)
            out[f"active_{window}"] = (hist > 0).sum(axis=1).astype("float32")

    last_seen = np.full(values.shape[0], 9999, dtype="float32")
    hist = values[:, :origin_pos]
    for row_idx, row in enumerate(hist):
        pos = np.flatnonzero(row > 0)
        if len(pos):
            last_seen[row_idx] = origin_pos - 1 - pos[-1]
    out["days_since_last_sale"] = last_seen
    if feature_date is None:
        feature_date = dates[min(origin_pos, len(dates) - 1)]
    out["origin_dow"] = np.full(values.shape[0], feature_date.dayofweek, dtype="int16")
    out["origin_month"] = np.full(values.shape[0], feature_date.month, dtype="int16")
    return out


def build_direct_dataset(
    y: pd.DataFrame,
    profile: pd.DataFrame,
    train_end: pd.Timestamp,
    cfg: LGBMConfig,
    item_codes: pd.Index,
    origins_end_offset: int = 0,
    filter_inactive: bool = False,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    dates = pd.DatetimeIndex(y.columns)
    values = y.loc[item_codes].to_numpy(dtype="float32", copy=False)
    date_to_pos = {date: idx for idx, date in enumerate(dates)}
    max_origin_pos = date_to_pos[train_end] - origins_end_offset
    min_origin_date = train_end - pd.Timedelta(days=cfg.origin_lookback_days)
    min_origin_pos = max(
        364,
        dates.searchsorted(min_origin_date),
    )
    origin_positions = list(range(min_origin_pos, max_origin_pos + 1, cfg.origin_stride))

    nonzero = values > 0
    has_any = nonzero.any(axis=1)
    first_sale_pos = np.where(has_any, nonzero.argmax(axis=1), len(dates))

    static = profile.loc[item_codes, [
        "profit_weight",
        "zero_ratio",
        "active_days",
        "avg_daily_qty",
        "avg_qty_when_active",
        "return_ratio",
    ]].reset_index(drop=True)
    item_id = np.arange(len(item_codes), dtype="int32")
    rows = []
    targets = []
    weights = []
    sku_weight = profile.loc[item_codes, "profit_weight"].to_numpy(dtype="float32")
    denom = rmsse_denominator(y.loc[item_codes, dates <= train_end]).to_numpy(dtype="float32")
    sample_weight = sku_weight / np.maximum(denom, 1e-9)
    sample_weight = sample_weight / np.mean(sample_weight[sample_weight > 0])

    for origin_pos in origin_positions:
        base_features = _origin_features(values, dates, origin_pos)
        # Convention: features at origin_pos cover values[:, :origin_pos] (exclusive),
        # so origin_pos itself is the first unknown / first-forecast day. Horizon h
        # targets values[origin_pos + h - 1], matching build_forecast_features which
        # sets origin_pos = pos_of(train_end) + 1 and forecasts train_end + h.
        valid_horizons = [
            horizon
            for horizon in range(1, cfg.horizon + 1)
            if origin_pos + horizon - 1 < len(dates)
            and dates[origin_pos + horizon - 1] <= train_end
        ]
        if not valid_horizons:
            continue

        n_items = len(item_codes)
        n_horizons = len(valid_horizons)
        frame = pd.DataFrame(
            {
                name: np.tile(feature_values, n_horizons)
                for name, feature_values in base_features.items()
            }
        )
        target_dates = pd.DatetimeIndex([dates[origin_pos + h - 1] for h in valid_horizons])
        frame["item_id"] = np.tile(item_id, n_horizons)
        frame["horizon"] = np.repeat(valid_horizons, n_items).astype("int16")
        frame["target_dow"] = np.repeat(target_dates.dayofweek.to_numpy(), n_items).astype("int16")
        frame["target_day"] = np.repeat(target_dates.day.to_numpy(), n_items).astype("int16")
        frame["target_month"] = np.repeat(target_dates.month.to_numpy(), n_items).astype("int16")
        frame["target_week"] = np.repeat(
            target_dates.isocalendar().week.to_numpy(dtype="int16"), n_items
        )
        frame = pd.concat(
            [
                frame.reset_index(drop=True),
                pd.concat([static] * n_horizons, ignore_index=True),
            ],
            axis=1,
        )
        target_matrix = np.vstack([values[:, origin_pos + h - 1] for h in valid_horizons])
        target_series = pd.Series(target_matrix.reshape(-1), dtype="float32")
        weight_series = pd.Series(np.tile(sample_weight, n_horizons), dtype="float32")
        if filter_inactive:
            active_sku_mask = first_sale_pos < origin_pos
            keep = np.tile(active_sku_mask, n_horizons)
            if not keep.all():
                frame = frame.loc[keep].reset_index(drop=True)
                target_series = target_series.loc[keep].reset_index(drop=True)
                weight_series = weight_series.loc[keep].reset_index(drop=True)
        rows.append(frame)
        targets.append(target_series)
        weights.append(weight_series)

    x = pd.concat(rows, ignore_index=True)
    target = pd.concat(targets, ignore_index=True)
    weight = pd.concat(weights, ignore_index=True).clip(lower=0)
    return x, target, weight


def build_forecast_features(
    y: pd.DataFrame,
    profile: pd.DataFrame,
    train_end: pd.Timestamp,
    horizon_dates: pd.DatetimeIndex,
    cfg: LGBMConfig,
    item_codes: pd.Index,
) -> pd.DataFrame:
    dates = pd.DatetimeIndex(y.columns)
    values = y.loc[item_codes].to_numpy(dtype="float32", copy=False)
    origin_pos = dates.get_loc(train_end) + 1
    base_features = _origin_features(
        values,
        dates,
        origin_pos,
        feature_date=train_end + pd.Timedelta(days=1),
    )
    static = profile.loc[item_codes, [
        "profit_weight",
        "zero_ratio",
        "active_days",
        "avg_daily_qty",
        "avg_qty_when_active",
        "return_ratio",
    ]].reset_index(drop=True)
    item_id = np.arange(len(item_codes), dtype="int32")
    rows = []
    for horizon, target_date in enumerate(horizon_dates, start=1):
        frame = pd.DataFrame(base_features)
        frame["item_id"] = item_id
        frame["horizon"] = horizon
        frame["target_dow"] = target_date.dayofweek
        frame["target_day"] = target_date.day
        frame["target_month"] = target_date.month
        frame["target_week"] = target_date.isocalendar().week
        frame = pd.concat([frame.reset_index(drop=True), static], axis=1)
        rows.append(frame)
    return pd.concat(rows, keys=horizon_dates, names=["Date"]).reset_index(level=0)


def train_and_evaluate(cfg: LGBMConfig = LGBMConfig()) -> dict[str, float | int | str]:
    artifact_dir = Path("data/artifacts")
    y = pd.read_pickle("data/processed/daily_demand_matrix.pkl")
    train_end = pd.Timestamp(cfg.valid_train_end)
    valid_dates = pd.date_range(train_end + pd.Timedelta(days=1), periods=cfg.horizon, freq="D")
    fit_train_end = train_end - pd.Timedelta(days=cfg.horizon)
    fit_profile = load_profile_for_date(y, fit_train_end, cfg)
    eval_profile = load_profile_for_date(y, train_end, cfg)
    item_codes = selected_skus(eval_profile, cfg)

    x_train, y_train, w_train = build_direct_dataset(
        y,
        fit_profile,
        fit_train_end,
        cfg,
        item_codes,
        filter_inactive=cfg.filter_inactive,
    )
    x_valid_train, y_valid_train, w_valid_train = build_direct_dataset(
        y,
        fit_profile,
        train_end,
        cfg,
        item_codes,
        origins_end_offset=cfg.horizon,
        filter_inactive=cfg.filter_inactive,
    )
    # Keep validation light while preserving recent origin behavior.
    x_valid_train = x_valid_train.tail(min(len(x_valid_train), cfg.top_n_skus * cfg.horizon * 8))
    y_valid_train = y_valid_train.loc[x_valid_train.index]
    w_valid_train = w_valid_train.loc[x_valid_train.index]

    params = {
        "objective": "tweedie",
        "tweedie_variance_power": 1.2,
        "metric": "rmse",
        "learning_rate": 0.035,
        "num_leaves": 63,
        "min_data_in_leaf": 80,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "lambda_l2": 2.0,
        "seed": cfg.random_seed,
        "num_threads": -1,
        "verbosity": -1,
    }
    train_set = lgb.Dataset(x_train, label=y_train, weight=w_train)
    valid_set = lgb.Dataset(x_valid_train, label=y_valid_train, weight=w_valid_train)
    model = lgb.train(
        params,
        train_set,
        num_boost_round=cfg.num_boost_round,
        valid_sets=[valid_set],
        callbacks=[
            lgb.early_stopping(cfg.early_stopping_rounds),
            lgb.log_evaluation(50),
        ],
    )

    x_pred = build_forecast_features(y, eval_profile, train_end, valid_dates, cfg, item_codes)
    pred_values = model.predict(x_pred.drop(columns=["Date"]), num_iteration=model.best_iteration)
    lgbm_top = pd.DataFrame(
        pred_values.reshape(len(valid_dates), len(item_codes)).T,
        index=item_codes,
        columns=valid_dates,
    ).clip(lower=0)

    train_y = y.loc[:, y.columns <= train_end]
    actual = y.loc[:, valid_dates]
    baseline = forecast_recent_median(train_y, valid_dates, 56)
    baseline_score, _ = wrmsse(actual, baseline, train_y, eval_profile["profit_weight"])
    blend_rows = []
    best_alpha = 0.0
    best_score = baseline_score
    best_hybrid = baseline
    for alpha in np.linspace(0, 1, 21):
        candidate = baseline.copy()
        blended_top = alpha * lgbm_top + (1 - alpha) * baseline.loc[item_codes, valid_dates]
        candidate.loc[item_codes, valid_dates] = blended_top.to_numpy(dtype="float32")
        candidate = candidate.clip(lower=0).astype("float32")
        score, _ = wrmsse(actual, candidate, train_y, eval_profile["profit_weight"])
        blend_rows.append({"alpha_lgbm": alpha, "wrmsse_56": score})
        if score < best_score:
            best_score = score
            best_alpha = float(alpha)
            best_hybrid = candidate

    hybrid = best_hybrid
    hybrid_score, sku_scores = wrmsse(actual, hybrid, train_y, eval_profile["profit_weight"])
    top_score, _ = wrmsse(
        actual.loc[item_codes],
        hybrid.loc[item_codes],
        train_y.loc[item_codes],
        eval_profile.loc[item_codes, "profit_weight"],
    )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    run_name = cfg.run_name
    model.save_model(str(artifact_dir / f"lgbm_direct_{run_name}.txt"))
    lgbm_top.to_pickle(artifact_dir / f"lgbm_valid_top_sku_forecast_{run_name}.pkl")
    hybrid.to_pickle(artifact_dir / f"hybrid_valid_forecast_{run_name}.pkl")
    pd.DataFrame(blend_rows).to_csv(
        artifact_dir / f"lgbm_blend_grid_{run_name}.csv", index=False
    )
    pd.DataFrame(
        {
            "feature": model.feature_name(),
            "importance_gain": model.feature_importance(importance_type="gain"),
            "importance_split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("importance_gain", ascending=False).to_csv(
        artifact_dir / f"lgbm_feature_importance_{run_name}.csv", index=False
    )
    sku_scores.sort_values("weighted_rmsse", ascending=False).to_csv(
        artifact_dir / f"hybrid_valid_sku_scores_{run_name}.csv"
    )
    pd.DataFrame(
        [
            {"model": "median_56", "wrmsse_56": baseline_score},
            {"model": f"hybrid_lgbm_top_{cfg.top_n_skus}", "wrmsse_56": hybrid_score},
            {"model": f"hybrid_lgbm_top_{cfg.top_n_skus}_renormalized", "wrmsse_56": top_score},
        ]
    ).to_csv(artifact_dir / f"lgbm_valid_scores_{run_name}.csv", index=False)

    print("Validation WRMSSE")
    print(f"median_56: {baseline_score:.6f}")
    print(f"hybrid_lgbm_top_{cfg.top_n_skus}: {hybrid_score:.6f}")
    print(f"best_alpha_lgbm: {best_alpha:.2f}")
    print(f"top_sku_renormalized_score: {top_score:.6f}")
    print(f"best_iteration: {model.best_iteration}")
    print(f"run_name: {run_name}")
    print(f"wrote {artifact_dir / f'lgbm_valid_scores_{run_name}.csv'}")
    return {
        "run_name": run_name,
        "top_n_skus": cfg.top_n_skus,
        "valid_train_end": cfg.valid_train_end,
        "baseline_wrmsse_56": baseline_score,
        "hybrid_wrmsse_56": hybrid_score,
        "best_alpha_lgbm": best_alpha,
        "top_sku_renormalized_score": top_score,
        "best_iteration": model.best_iteration,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-n-skus", type=int, default=LGBMConfig.top_n_skus)
    parser.add_argument("--lookback-days", type=int, default=LGBMConfig.origin_lookback_days)
    parser.add_argument("--origin-stride", type=int, default=LGBMConfig.origin_stride)
    parser.add_argument("--valid-train-end", type=str, default=LGBMConfig.valid_train_end)
    parser.add_argument("--num-boost-round", type=int, default=LGBMConfig.num_boost_round)
    parser.add_argument(
        "--early-stopping-rounds", type=int, default=LGBMConfig.early_stopping_rounds
    )
    parser.add_argument("--sku-strategy", type=str, default=LGBMConfig.sku_strategy)
    parser.add_argument("--min-active-days", type=int, default=LGBMConfig.min_active_days)
    parser.add_argument(
        "--max-days-since-last-sale", type=int, default=LGBMConfig.max_days_since_last_sale
    )
    parser.add_argument("--filter-inactive", action="store_true")
    parser.add_argument("--use-global-profile", action="store_true")
    args = parser.parse_args()
    train_and_evaluate(
        LGBMConfig(
            top_n_skus=args.top_n_skus,
            origin_lookback_days=args.lookback_days,
            origin_stride=args.origin_stride,
            valid_train_end=args.valid_train_end,
            num_boost_round=args.num_boost_round,
            early_stopping_rounds=args.early_stopping_rounds,
            sku_strategy=args.sku_strategy,
            min_active_days=args.min_active_days,
            max_days_since_last_sale=args.max_days_since_last_sale,
            filter_inactive=args.filter_inactive,
            time_consistent_profile=not args.use_global_profile,
        )
    )
