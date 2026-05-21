from __future__ import annotations

from pathlib import Path

from hbacc_prj.baselines import evaluate_baselines
from hbacc_prj.data import load_train, make_daily_sales, make_demand_matrix, make_sku_profile


def main() -> None:
    artifact_dir = Path("data/artifacts")
    processed_dir = Path("data/processed")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    train = load_train("data/raw/train.csv")
    daily = make_daily_sales(train)
    y = make_demand_matrix(daily)
    profile = make_sku_profile(train, y)
    scores = evaluate_baselines(y, profile["profit_weight"], daily=daily)

    daily.to_pickle(processed_dir / "daily_sales.pkl")
    y.to_pickle(processed_dir / "daily_demand_matrix.pkl")
    profile.to_pickle(artifact_dir / "sku_profile.pkl")
    scores.to_csv(artifact_dir / "baseline_cv_scores.csv", index=False)

    summary = (
        scores.groupby("model", as_index=False)[["wrmsse_28", "wrmsse_56"]]
        .mean()
        .sort_values("wrmsse_56")
    )
    print("Baseline CV mean WRMSSE")
    print(summary.to_string(index=False))
    print(f"\nWrote {processed_dir / 'daily_sales.pkl'}")
    print(f"Wrote {processed_dir / 'daily_demand_matrix.pkl'}")
    print(f"Wrote {artifact_dir / 'sku_profile.pkl'}")
    print(f"Wrote {artifact_dir / 'baseline_cv_scores.csv'}")


if __name__ == "__main__":
    main()
