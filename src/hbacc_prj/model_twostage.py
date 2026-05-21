from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from hbacc_prj.baselines import forecast_recent_median
from hbacc_prj.metrics import wrmsse
from hbacc_prj.model_lgbm import (
    LGBMConfig,
    build_direct_dataset,
    build_forecast_features,
    load_profile_for_date,
    selected_skus,
)


VALUE_COLUMNS = [f"F{i}" for i in range(1, 29)]


@dataclass(frozen=True)
class TwoStageConfig:
    top_n_skus: int = 300
    origin_lookback_days: int = 365
    origin_stride: int = 14
    horizon: int = 56
    valid_train_end: str = "2025-07-11"
    num_boost_round: int = 450
    early_stopping_rounds: int = 50
    random_seed: int = 2027
    sku_strategy: str = "top_profit"
    min_active_days: int = 50
    max_days_since_last_sale: int = 56
    time_consistent_profile: bool = True
    zero_sundays: bool = True
    end_of_selling_since: str = "2025-01-01"
    recursive_forecast: bool = False

    @property
    def lgbm_config(self) -> LGBMConfig:
        return LGBMConfig(
            top_n_skus=self.top_n_skus,
            horizon=self.horizon,
            origin_lookback_days=self.origin_lookback_days,
            origin_stride=self.origin_stride,
            valid_train_end=self.valid_train_end,
            num_boost_round=self.num_boost_round,
            early_stopping_rounds=self.early_stopping_rounds,
            random_seed=self.random_seed,
            sku_strategy=self.sku_strategy,
            min_active_days=self.min_active_days,
            max_days_since_last_sale=self.max_days_since_last_sale,
            time_consistent_profile=self.time_consistent_profile,
        )

    @property
    def run_name(self) -> str:
        profile_suffix = "_tc" if self.time_consistent_profile else ""
        calendar_suffix = "_sun0" if self.zero_sundays else ""
        eos_suffix = "_eos0" if self.end_of_selling_since else ""
        recursive_suffix = "_rec" if self.recursive_forecast else ""
        boost_suffix = "_b900" if self.num_boost_round != 450 else ""
        return (
            f"twostage_{self.sku_strategy}_top{self.top_n_skus}"
            f"_a{self.min_active_days}_r{self.max_days_since_last_sale}"
            f"_lb{self.origin_lookback_days}"
            f"_s{self.origin_stride}{profile_suffix}{calendar_suffix}{eos_suffix}"
            f"{recursive_suffix}{boost_suffix}"
            f"_{self.valid_train_end}"
        )


def _train_models(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    w_train: pd.Series,
    x_valid: pd.DataFrame,
    y_valid: pd.Series,
    w_valid: pd.Series,
    cfg: TwoStageConfig,
) -> tuple[lgb.Booster, lgb.Booster]:
    clf_params = {
        "objective": "binary",
        "metric": "binary_logloss",
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
    reg_params = {
        "objective": "poisson",
        "metric": "rmse",
        "learning_rate": 0.035,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "lambda_l2": 2.0,
        "seed": cfg.random_seed + 1,
        "num_threads": -1,
        "verbosity": -1,
    }

    y_binary = (y_train > 0).astype("int8")
    y_valid_binary = (y_valid > 0).astype("int8")
    classifier = lgb.train(
        clf_params,
        lgb.Dataset(x_train, label=y_binary, weight=w_train),
        num_boost_round=cfg.num_boost_round,
        valid_sets=[lgb.Dataset(x_valid, label=y_valid_binary, weight=w_valid)],
        callbacks=[
            lgb.early_stopping(cfg.early_stopping_rounds),
            lgb.log_evaluation(75),
        ],
    )

    positive_mask = y_train > 0
    valid_positive_mask = y_valid > 0
    regressor = lgb.train(
        reg_params,
        lgb.Dataset(
            x_train.loc[positive_mask],
            label=y_train.loc[positive_mask],
            weight=w_train.loc[positive_mask],
        ),
        num_boost_round=cfg.num_boost_round,
        valid_sets=[
            lgb.Dataset(
                x_valid.loc[valid_positive_mask],
                label=y_valid.loc[valid_positive_mask],
                weight=w_valid.loc[valid_positive_mask],
            )
        ],
        callbacks=[
            lgb.early_stopping(cfg.early_stopping_rounds),
            lgb.log_evaluation(75),
        ],
    )
    return classifier, regressor


def _predict_twostage(
    classifier: lgb.Booster,
    regressor: lgb.Booster,
    x_pred: pd.DataFrame,
    item_codes: pd.Index,
    horizon_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    features = x_pred.drop(columns=["Date"])
    sale_prob = classifier.predict(features, num_iteration=classifier.best_iteration)
    qty_if_sale = regressor.predict(features, num_iteration=regressor.best_iteration)
    pred_values = np.clip(sale_prob * qty_if_sale, 0, None)
    return pd.DataFrame(
        pred_values.reshape(len(horizon_dates), len(item_codes)).T,
        index=item_codes,
        columns=horizon_dates,
    ).astype("float32")


def _predict_twostage_values(
    classifier: lgb.Booster,
    regressor: lgb.Booster,
    x_pred: pd.DataFrame,
) -> np.ndarray:
    features = x_pred.drop(columns=["Date"])
    sale_prob = classifier.predict(features, num_iteration=classifier.best_iteration)
    qty_if_sale = regressor.predict(features, num_iteration=regressor.best_iteration)
    return np.clip(sale_prob * qty_if_sale, 0, None)


def evaluate(cfg: TwoStageConfig) -> dict[str, float | int | str]:
    artifact_dir = Path("data/artifacts")
    y = pd.read_pickle("data/processed/daily_demand_matrix.pkl")
    train_end = pd.Timestamp(cfg.valid_train_end)
    valid_dates = pd.date_range(train_end + pd.Timedelta(days=1), periods=cfg.horizon, freq="D")
    lgbm_cfg = cfg.lgbm_config
    fit_train_end = train_end - pd.Timedelta(days=cfg.horizon)
    fit_profile = load_profile_for_date(y, fit_train_end, lgbm_cfg)
    eval_profile = load_profile_for_date(y, train_end, lgbm_cfg)
    item_codes = selected_skus(eval_profile, lgbm_cfg)

    x_train, y_train, w_train = build_direct_dataset(
        y, fit_profile, fit_train_end, lgbm_cfg, item_codes
    )
    x_valid, y_valid, w_valid = build_direct_dataset(
        y,
        fit_profile,
        train_end,
        lgbm_cfg,
        item_codes,
        origins_end_offset=cfg.horizon,
    )
    x_valid = x_valid.tail(min(len(x_valid), cfg.top_n_skus * cfg.horizon * 8))
    y_valid = y_valid.loc[x_valid.index]
    w_valid = w_valid.loc[x_valid.index]

    train_y = y.loc[:, y.columns <= train_end]
    actual = y.loc[:, valid_dates]
    baseline = forecast_recent_median(train_y, valid_dates, 56)
    baseline = postprocess_forecast(baseline, train_y, cfg)
    baseline_score, _ = wrmsse(actual, baseline, train_y, eval_profile["profit_weight"])

    classifier, regressor = _train_models(x_train, y_train, w_train, x_valid, y_valid, w_valid, cfg)
    twostage_top = None
    if not cfg.recursive_forecast:
        x_pred = build_forecast_features(y, eval_profile, train_end, valid_dates, lgbm_cfg, item_codes)
        twostage_top = _predict_twostage(classifier, regressor, x_pred, item_codes, valid_dates)

    rows = []
    best_alpha = 0.0
    best_score = baseline_score
    best_forecast = baseline
    for alpha in np.linspace(0, 1, 21):
        if cfg.recursive_forecast:
            candidate = forecast_alpha_recursive(
                classifier,
                regressor,
                train_y,
                eval_profile,
                train_end,
                valid_dates,
                cfg,
                lgbm_cfg,
                item_codes,
                float(alpha),
            )
        else:
            candidate = baseline.copy()
            blended = alpha * twostage_top + (1 - alpha) * baseline.loc[item_codes, valid_dates]
            candidate.loc[item_codes, valid_dates] = blended.to_numpy(dtype="float32")
            candidate = candidate.clip(lower=0).astype("float32")
            candidate = postprocess_forecast(candidate, train_y, cfg)
        score, _ = wrmsse(actual, candidate, train_y, eval_profile["profit_weight"])
        rows.append({"alpha_twostage": float(alpha), "wrmsse_56": score})
        if score < best_score:
            best_alpha = float(alpha)
            best_score = score
            best_forecast = candidate

    artifact_dir.mkdir(parents=True, exist_ok=True)
    run_name = cfg.run_name
    classifier.save_model(str(artifact_dir / f"classifier_{run_name}.txt"))
    regressor.save_model(str(artifact_dir / f"regressor_{run_name}.txt"))
    if twostage_top is not None:
        twostage_top.to_pickle(artifact_dir / f"twostage_valid_top_sku_forecast_{run_name}.pkl")
    best_forecast.to_pickle(artifact_dir / f"twostage_hybrid_valid_forecast_{run_name}.pkl")
    pd.DataFrame(rows).to_csv(artifact_dir / f"twostage_blend_grid_{run_name}.csv", index=False)

    result = {
        "run_name": run_name,
        "baseline_wrmsse_56": baseline_score,
        "twostage_wrmsse_56": best_score,
        "best_alpha_twostage": best_alpha,
        "classifier_best_iteration": classifier.best_iteration,
        "regressor_best_iteration": regressor.best_iteration,
    }
    pd.DataFrame([result]).to_csv(artifact_dir / f"twostage_valid_scores_{run_name}.csv", index=False)
    print("Two-stage validation WRMSSE")
    print(f"median_56: {baseline_score:.6f}")
    print(f"twostage_hybrid: {best_score:.6f}")
    print(f"best_alpha_twostage: {best_alpha:.2f}")
    print(f"run_name: {run_name}")
    return result


def _to_competition_wide(forecast: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for suffix, window in [
        ("validation", forecast.iloc[:, :28]),
        ("evaluation", forecast.iloc[:, 28:56]),
    ]:
        part = window.copy()
        part.columns = VALUE_COLUMNS
        part.insert(0, "id", [f"{sku}_{suffix}" for sku in part.index])
        rows.append(part.reset_index(drop=True))
    return pd.concat(rows, ignore_index=True)


def zero_sunday_forecast(forecast: pd.DataFrame) -> pd.DataFrame:
    """Set structurally closed Sundays to zero.

    EDA showed no sales transactions on Sundays from 2023 onward. All current
    validation and future windows are after 2023, so Sunday demand is treated as
    closed-business noise instead of a learnable demand pattern.
    """
    out = forecast.copy()
    sunday_cols = [col for col in pd.DatetimeIndex(out.columns) if col.dayofweek == 6]
    if sunday_cols:
        out.loc[:, sunday_cols] = 0.0
    return out


def zero_end_of_selling_forecast(
    forecast: pd.DataFrame,
    train_y: pd.DataFrame,
    since: str,
) -> pd.DataFrame:
    """Set SKUs with no sales since `since` to zero."""
    if not since:
        return forecast
    columns = pd.DatetimeIndex(train_y.columns)
    recent = train_y.loc[:, columns >= pd.Timestamp(since)]
    if recent.empty:
        return forecast
    end_selling = recent.sum(axis=1).eq(0)
    skus = end_selling[end_selling].index.intersection(forecast.index)
    if len(skus) == 0:
        return forecast
    out = forecast.copy()
    out.loc[skus, :] = 0.0
    return out


def postprocess_forecast(
    forecast: pd.DataFrame,
    train_y: pd.DataFrame,
    cfg: TwoStageConfig,
) -> pd.DataFrame:
    out = forecast
    if cfg.zero_sundays:
        out = zero_sunday_forecast(out)
    if cfg.end_of_selling_since:
        out = zero_end_of_selling_forecast(out, train_y, cfg.end_of_selling_since)
    return out


def forecast_alpha_recursive(
    classifier: lgb.Booster,
    regressor: lgb.Booster,
    y_history: pd.DataFrame,
    profile: pd.DataFrame,
    train_end: pd.Timestamp,
    horizon_dates: pd.DatetimeIndex,
    cfg: TwoStageConfig,
    lgbm_cfg: LGBMConfig,
    item_codes: pd.Index,
    alpha: float,
) -> pd.DataFrame:
    """Build an alpha-blended forecast one day at a time.

    The submitted/blended forecast is appended to the temporary history before
    predicting the next day, so lag and rolling features for later horizons see
    the same values that would be available in a true recursive forecast.
    """
    y_work = y_history.copy()
    forecast_parts = []
    for target_date in horizon_dates:
        previous_date = pd.DatetimeIndex(y_work.columns).max()
        x_pred = build_forecast_features(
            y_work,
            profile,
            previous_date,
            pd.DatetimeIndex([target_date]),
            lgbm_cfg,
            item_codes,
        )
        pred_values = _predict_twostage_values(classifier, regressor, x_pred).astype("float32")
        baseline = forecast_recent_median(y_work, pd.DatetimeIndex([target_date]), 56)
        candidate = baseline.copy()
        blended = alpha * pred_values + (1 - alpha) * baseline.loc[item_codes, target_date].to_numpy(
            dtype="float32"
        )
        candidate.loc[item_codes, target_date] = blended
        candidate = candidate.clip(lower=0).astype("float32")
        candidate = postprocess_forecast(candidate, y_work, cfg)
        forecast_parts.append(candidate)

        next_col = candidate.loc[y_work.index, target_date].astype("float32")
        y_work = pd.concat([y_work, next_col.to_frame(name=target_date)], axis=1)

    return pd.concat(forecast_parts, axis=1).astype("float32")


def forecast_future(cfg: TwoStageConfig, alphas: list[float]) -> None:
    artifact_dir = Path("data/artifacts")
    sample = pd.read_csv("data/raw/sample_submission.csv")
    y = pd.read_pickle("data/processed/daily_demand_matrix.pkl")
    train_end = pd.DatetimeIndex(y.columns).max()
    horizon_dates = pd.date_range(train_end + pd.Timedelta(days=1), periods=cfg.horizon, freq="D")
    lgbm_cfg = cfg.lgbm_config
    profile = load_profile_for_date(y, train_end, lgbm_cfg)
    item_codes = selected_skus(profile, lgbm_cfg)

    x_train, y_train, w_train = build_direct_dataset(y, profile, train_end, lgbm_cfg, item_codes)
    x_valid, y_valid, w_valid = build_direct_dataset(
        y,
        profile,
        train_end,
        lgbm_cfg,
        item_codes,
        origins_end_offset=cfg.horizon,
    )
    x_valid = x_valid.tail(min(len(x_valid), cfg.top_n_skus * cfg.horizon * 8))
    y_valid = y_valid.loc[x_valid.index]
    w_valid = w_valid.loc[x_valid.index]

    baseline = forecast_recent_median(y, horizon_dates, 56)
    baseline = postprocess_forecast(baseline, y, cfg)
    classifier, regressor = _train_models(x_train, y_train, w_train, x_valid, y_valid, w_valid, cfg)
    twostage_top = None
    if not cfg.recursive_forecast:
        x_pred = build_forecast_features(y, profile, train_end, horizon_dates, lgbm_cfg, item_codes)
        twostage_top = _predict_twostage(classifier, regressor, x_pred, item_codes, horizon_dates)

    run_name = cfg.run_name
    classifier.save_model(str(artifact_dir / f"classifier_future_{run_name}.txt"))
    regressor.save_model(str(artifact_dir / f"regressor_future_{run_name}.txt"))
    if twostage_top is not None:
        twostage_top.to_pickle(artifact_dir / f"twostage_future_top_sku_forecast_{run_name}.pkl")

    for alpha in alphas:
        if cfg.recursive_forecast:
            forecast = forecast_alpha_recursive(
                classifier,
                regressor,
                y,
                profile,
                train_end,
                horizon_dates,
                cfg,
                lgbm_cfg,
                item_codes,
                float(alpha),
            )
        else:
            forecast = baseline.copy()
            blended = alpha * twostage_top + (1 - alpha) * baseline.loc[item_codes, horizon_dates]
            forecast.loc[item_codes, horizon_dates] = blended.to_numpy(dtype="float32")
            forecast = forecast.clip(lower=0).astype("float32")
            forecast = postprocess_forecast(forecast, y, cfg)
        wide = _to_competition_wide(forecast)
        submission = sample[["id"]].merge(wide, on="id", how="left")
        if submission[VALUE_COLUMNS].isna().any().any():
            raise ValueError("future two-stage submission has missing ids")
        submission[VALUE_COLUMNS] = submission[VALUE_COLUMNS].clip(lower=0).astype("float32")
        out_path = artifact_dir / f"submission_{run_name}_alpha{alpha:.2f}.csv"
        submission.to_csv(out_path, index=False)
        print(
            f"{out_path.name}: rows={len(submission):,}, "
            f"sum={float(submission[VALUE_COLUMNS].sum().sum()):.2f}, "
            f"min={float(submission[VALUE_COLUMNS].min().min()):.6f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["evaluate", "future"], default="evaluate")
    parser.add_argument("--top-n-skus", type=int, default=TwoStageConfig.top_n_skus)
    parser.add_argument("--valid-train-end", type=str, default=TwoStageConfig.valid_train_end)
    parser.add_argument("--lookback-days", type=int, default=TwoStageConfig.origin_lookback_days)
    parser.add_argument("--origin-stride", type=int, default=TwoStageConfig.origin_stride)
    parser.add_argument("--num-boost-round", type=int, default=TwoStageConfig.num_boost_round)
    parser.add_argument(
        "--early-stopping-rounds", type=int, default=TwoStageConfig.early_stopping_rounds
    )
    parser.add_argument("--sku-strategy", type=str, default=TwoStageConfig.sku_strategy)
    parser.add_argument("--min-active-days", type=int, default=TwoStageConfig.min_active_days)
    parser.add_argument(
        "--max-days-since-last-sale",
        type=int,
        default=TwoStageConfig.max_days_since_last_sale,
    )
    parser.add_argument("--alphas", type=str, default="0.50,0.70,0.80,1.00")
    parser.add_argument("--use-global-profile", action="store_true")
    parser.add_argument("--keep-sundays", action="store_true")
    parser.add_argument("--end-of-selling-since", type=str, default=TwoStageConfig.end_of_selling_since)
    parser.add_argument("--recursive-forecast", action="store_true")
    args = parser.parse_args()

    cfg = TwoStageConfig(
        top_n_skus=args.top_n_skus,
        valid_train_end=args.valid_train_end,
        origin_lookback_days=args.lookback_days,
        origin_stride=args.origin_stride,
        num_boost_round=args.num_boost_round,
        early_stopping_rounds=args.early_stopping_rounds,
        sku_strategy=args.sku_strategy,
        min_active_days=args.min_active_days,
        max_days_since_last_sale=args.max_days_since_last_sale,
        time_consistent_profile=not args.use_global_profile,
        zero_sundays=not args.keep_sundays,
        end_of_selling_since=args.end_of_selling_since,
        recursive_forecast=args.recursive_forecast,
    )
    if args.mode == "evaluate":
        evaluate(cfg)
    else:
        alphas = [float(value) for value in args.alphas.split(",") if value]
        forecast_future(cfg, alphas)


if __name__ == "__main__":
    main()
