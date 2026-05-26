from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from hbacc_prj.pipeline import run_pipeline_stage


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build raw, bronze, silver and gold sales tables."
    )
    parser.add_argument("--source-path", type=Path, default=Path("data/raw/train.csv"))
    parser.add_argument("--batch-id", default="train-full")
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=Path("scripts/sprint_02_pipeline_schema.sql"),
    )
    parser.add_argument(
        "--stage",
        choices=["validate", "raw", "bronze", "silver", "gold", "quality", "all"],
        default="all",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL",
            "postgresql://forecast:forecast-local-only@localhost:5432/sku_forecasting",
        ),
    )
    args = parser.parse_args()

    summary = run_pipeline_stage(
        args.database_url,
        args.source_path,
        args.batch_id,
        args.schema_path,
        args.stage,
    )
    print(
        json.dumps(
            {"stage": args.stage, "batch_id": args.batch_id, **summary}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
