from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg

from hbacc_prj.monitoring import DEFAULT_FEATURE_VERSION, dump_report, run_monitoring


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Sprint 7 monitoring reports.")
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql://forecast:forecast-local-only@localhost:5432/sku_forecasting",
        ),
    )
    parser.add_argument("--report-id", default="sprint-07-monitoring-manual")
    parser.add_argument("--feature-version", default=DEFAULT_FEATURE_VERSION)
    parser.add_argument("--output-dir", type=Path, default=Path("data/monitoring"))
    parser.add_argument(
        "--schema-file",
        type=Path,
        default=Path("scripts/sprint_07_monitoring_schema.sql"),
    )
    args = parser.parse_args()

    with psycopg.connect(args.database_url) as connection:
        connection.execute(args.schema_file.read_text(encoding="utf-8"))
        report = run_monitoring(
            connection, args.report_id, args.output_dir, args.feature_version
        )
    dump_report(report, args.output_dir / f"{args.report_id}.json")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
