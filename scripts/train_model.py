from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from hbacc_prj.training import train_and_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Train LightGBM and log to MLflow.")
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql://forecast:forecast-local-only@localhost:5432/sku_forecasting",
        ),
    )
    parser.add_argument(
        "--tracking-uri",
        default=os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"),
    )
    parser.add_argument("--feature-version", default="sprint-03-v1-top100-a60-h56")
    parser.add_argument("--validation-days", type=int, default=28)
    parser.add_argument("--random-seed", type=int, default=2026)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("data/features/evaluation_sprint_04.json"),
    )
    args = parser.parse_args()
    report = train_and_log(
        args.database_url,
        args.tracking_uri,
        args.feature_version,
        args.output_path,
        validation_days=args.validation_days,
        random_seed=args.random_seed,
    )
    summary = {
        "feature_version": report["feature_version"],
        "mlflow_run_id": report["mlflow_run_id"],
        "lightgbm_wape": report["metrics"]["lightgbm"]["wape"],
        "best_baseline_wape": report["best_baseline_wape"],
        "passed_registration_rule": report["passed_registration_rule"],
        "registered_model_version": report["registered_model_version"],
        "model_reloaded_and_predicted": report["model_reloaded_and_predicted"],
    }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
