from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from hbacc_prj.features import run_feature_stage


def main() -> None:
    parser = argparse.ArgumentParser(description="Build offline SKU feature snapshot.")
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql://forecast:forecast-local-only@localhost:5432/sku_forecasting",
        ),
    )
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=Path("scripts/sprint_03_feature_schema.sql"),
    )
    parser.add_argument("--feature-version", default="sprint-03-v1-top100-a60-h56")
    parser.add_argument("--max-skus", type=int, default=100)
    parser.add_argument("--as-of-days", type=int, default=60)
    parser.add_argument("--max-horizon", type=int, default=56)
    parser.add_argument(
        "--stage",
        choices=["validate-gold", "save", "quality", "summary", "all"],
        default="all",
    )
    args = parser.parse_args()
    summary = run_feature_stage(
        args.database_url,
        args.schema_path,
        args.feature_version,
        args.max_skus,
        args.as_of_days,
        args.max_horizon,
        args.stage,
    )
    summary["stage"] = args.stage
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
