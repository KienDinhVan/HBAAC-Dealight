from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

import psycopg

from hbacc_prj.forecasting import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAX_HORIZON,
    generate_and_save,
)


def apply_schema(database_url: str, schema_file: Path) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(schema_file.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Sprint 5 batch forecasting and persist results."
    )
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
    parser.add_argument(
        "--model-name", default=os.getenv("MODEL_NAME", "sku-demand-lightgbm")
    )
    parser.add_argument(
        "--feature-version",
        default=os.getenv("FEATURE_VERSION", "sprint-03-v1-top100-a60-h56"),
    )
    parser.add_argument(
        "--forecast-date",
        default=os.getenv("FORECAST_DATE", "2025-09-05"),
        help="ISO date YYYY-MM-DD",
    )
    parser.add_argument(
        "--run-id",
        default=os.getenv("FORECAST_RUN_ID", "sprint-05-lightgbm-v5"),
    )
    parser.add_argument("--max-horizon", type=int, default=DEFAULT_MAX_HORIZON)
    parser.add_argument(
        "--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
        help="Window of Gold history loaded for feature computation.",
    )
    parser.add_argument(
        "--sku-lookback-days",
        type=int,
        default=None,
        help="Activity window for SKU universe. <=0 means all SKUs ever seen.",
    )
    parser.add_argument(
        "--schema-file",
        type=Path,
        default=Path("scripts/sprint_05_serving_schema.sql"),
    )
    args = parser.parse_args()

    apply_schema(args.database_url, args.schema_file)

    summary = generate_and_save(
        database_url=args.database_url,
        tracking_uri=args.tracking_uri,
        model_name=args.model_name,
        feature_version=args.feature_version,
        forecast_date=date.fromisoformat(args.forecast_date),
        run_id=args.run_id,
        max_horizon=args.max_horizon,
        lookback_days=args.lookback_days,
        sku_lookback_days=args.sku_lookback_days,
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
