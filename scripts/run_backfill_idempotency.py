"""Backfill idempotency evidence (E6).

Runs the full ingest -> bronze -> silver -> gold pipeline TWICE for the same
batch_id and compares row-counts and content checksums for every layer.

Because every pipeline stage performs ``DELETE ... WHERE batch_id`` (gold does a
full delete+reload) before re-loading, a second backfill MUST produce identical
row-counts and identical content. Volatile audit columns (``ingested_at`` /
``transformed_at`` / ``created_at``) are excluded from the checksum so only the
business payload is compared.

Usage:
    python -m scripts.run_backfill_idempotency \
        --source-path data/raw/train.csv \
        --schema-path scripts/sprint_02_pipeline_schema.sql \
        --batch-id backfill-demo
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from hbacc_prj.pipeline import run_pipeline_stage

# Business columns per layer (audit/timestamp columns deliberately excluded so
# the checksum reflects data content, not wall-clock load time).
LAYER_QUERIES: dict[str, str] = {
    "raw.transactions": (
        "SELECT count(*), md5(coalesce(string_agg(rowhash, '' ORDER BY ord), '')) "
        "FROM (SELECT md5(row(batch_id, source_file, source_row_number, date_raw, "
        "stt_raw, item_code_raw, quantity_raw, unit_price_raw, sales_amount_raw, "
        "unit_cost_raw, cost_amount_raw)::text) AS rowhash, source_row_number AS ord "
        "FROM raw.transactions WHERE batch_id = %(batch)s) s"
    ),
    "bronze.transactions": (
        "SELECT count(*), md5(coalesce(string_agg(rowhash, '' ORDER BY ord), '')) "
        "FROM (SELECT md5(row(batch_id, source_file, source_row_number, stt, date_raw, "
        "item_code_raw, quantity, unit_price_raw, sales_amount, unit_cost_raw, "
        "cost_amount)::text) AS rowhash, source_row_number AS ord "
        "FROM bronze.transactions WHERE batch_id = %(batch)s) s"
    ),
    "silver.transactions_clean": (
        "SELECT count(*), md5(coalesce(string_agg(rowhash, '' ORDER BY ord), '')) "
        "FROM (SELECT md5(row(batch_id, source_row_number, date, item_code, quantity, "
        "sales_quantity, return_quantity, unit_price, sales_amount, unit_cost, "
        "cost_amount, is_return, is_valid, error_reason)::text) AS rowhash, "
        "source_row_number AS ord "
        "FROM silver.transactions_clean WHERE batch_id = %(batch)s) s"
    ),
    "gold.daily_sku_sales": (
        "SELECT count(*), md5(coalesce(string_agg(rowhash, '' ORDER BY ord), '')) "
        "FROM (SELECT md5(row(date, item_code, quantity_sold, return_quantity, "
        "net_quantity, sales_amount, cost_amount, avg_unit_price, avg_unit_cost, "
        "transaction_count)::text) AS rowhash, "
        "(date::text || '|' || item_code) AS ord "
        "FROM gold.daily_sku_sales) s"
    ),
}


def compute_layer_fingerprints(
    database_url: str, batch_id: str
) -> dict[str, dict[str, Any]]:
    """Return {layer: {"rows": int, "checksum": str}} for every layer."""
    fingerprints: dict[str, dict[str, Any]] = {}
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for layer, query in LAYER_QUERIES.items():
                cursor.execute(query, {"batch": batch_id})
                rows, checksum = cursor.fetchone()  # type: ignore[misc]
                fingerprints[layer] = {
                    "rows": int(rows),
                    "checksum": checksum or "(empty)",
                }
    return fingerprints


def run_backfill(
    database_url: str,
    source_path: Path,
    batch_id: str,
    schema_path: Path,
    label: str,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    summary = run_pipeline_stage(
        database_url, source_path, batch_id, schema_path, "all"
    )
    finished = datetime.now(timezone.utc)
    fingerprints = compute_layer_fingerprints(database_url, batch_id)
    return {
        "label": label,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "pipeline_summary": summary,
        "fingerprints": fingerprints,
    }


def _print_comparison(run1: dict[str, Any], run2: dict[str, Any]) -> bool:
    print("\n" + "=" * 78)
    print("  BACKFILL IDEMPOTENCY EVIDENCE (E6)")
    print("=" * 78)
    print(f"  Run #1 ({run1['label']}): {run1['duration_seconds']}s @ {run1['started_at']}")
    print(f"  Run #2 ({run2['label']}): {run2['duration_seconds']}s @ {run2['started_at']}")
    print("-" * 78)
    header = f"  {'LAYER':<28}{'RUN1 ROWS':>11}{'RUN2 ROWS':>11}  {'CHECKSUM MATCH':<16}"
    print(header)
    print("-" * 78)

    all_ok = True
    for layer in LAYER_QUERIES:
        f1 = run1["fingerprints"][layer]
        f2 = run2["fingerprints"][layer]
        rows_ok = f1["rows"] == f2["rows"]
        sum_ok = f1["checksum"] == f2["checksum"]
        ok = rows_ok and sum_ok
        all_ok = all_ok and ok
        status = "IDENTICAL  OK" if ok else "MISMATCH  FAIL"
        print(
            f"  {layer:<28}{f1['rows']:>11}{f2['rows']:>11}  {status:<16}"
        )
        print(f"      run1 md5={f1['checksum']}")
        print(f"      run2 md5={f2['checksum']}")
    print("-" * 78)
    verdict = "PASS - pipeline is IDEMPOTENT" if all_ok else "FAIL - non-deterministic output"
    print(f"  RESULT: {verdict}")
    print("=" * 78 + "\n")
    return all_ok


def _load_run(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill idempotency evidence (E6).")
    parser.add_argument(
        "--mode",
        choices=["both", "run", "compare"],
        default="both",
        help=(
            "both = run twice + compare in one process (standalone). "
            "run = single backfill pass, write fingerprint to --fingerprint-out "
            "(used by the Airflow DAG tasks). compare = read two fingerprint files "
            "(--fingerprint-in) and print the idempotency comparison."
        ),
    )
    parser.add_argument("--source-path", type=Path, default=Path("data/raw/train.csv"))
    parser.add_argument("--batch-id", default="backfill-demo")
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=Path("scripts/sprint_02_pipeline_schema.sql"),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL",
            "postgresql://forecast:forecast-local-only@localhost:5432/sku_forecasting",
        ),
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("data/backfill_idempotency.json"),
    )
    parser.add_argument("--run-label", default="first", help="Label for --mode run.")
    parser.add_argument(
        "--fingerprint-out",
        type=Path,
        help="Where --mode run writes its fingerprint JSON.",
    )
    parser.add_argument(
        "--fingerprint-in",
        type=Path,
        nargs=2,
        help="Two fingerprint files for --mode compare.",
    )
    args = parser.parse_args()

    # --- Airflow task: one backfill pass, persist fingerprint to a file. ---
    if args.mode == "run":
        print(f">>> Backfill pass '{args.run_label}' (batch_id={args.batch_id}) ...")
        result = run_backfill(
            args.database_url,
            args.source_path,
            args.batch_id,
            args.schema_path,
            args.run_label,
        )
        print(f"    rows: {result['pipeline_summary']}")
        if args.fingerprint_out:
            args.fingerprint_out.parent.mkdir(parents=True, exist_ok=True)
            args.fingerprint_out.write_text(
                json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(f"    fingerprint -> {args.fingerprint_out}")
        return

    # --- Airflow task: compare two persisted fingerprints. ---
    if args.mode == "compare":
        if not args.fingerprint_in:
            raise SystemExit("--mode compare requires --fingerprint-in <f1> <f2>")
        run1 = _load_run(args.fingerprint_in[0])
        run2 = _load_run(args.fingerprint_in[1])
        idempotent = _print_comparison(run1, run2)
        raise SystemExit(0 if idempotent else 1)

    # --- Standalone (default): run twice + compare in one process. ---
    print(f">>> Backfill run #1 (batch_id={args.batch_id}) ...")
    run1 = run_backfill(
        args.database_url, args.source_path, args.batch_id, args.schema_path, "first"
    )
    print(f"    rows: {run1['pipeline_summary']}")

    print(f">>> Backfill run #2 (re-run same batch_id={args.batch_id}) ...")
    run2 = run_backfill(
        args.database_url, args.source_path, args.batch_id, args.schema_path, "second"
    )
    print(f"    rows: {run2['pipeline_summary']}")

    idempotent = _print_comparison(run1, run2)

    try:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(
            json.dumps(
                {"idempotent": idempotent, "run_1": run1, "run_2": run2},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"Report written to {args.report_path}")
    except OSError as exc:
        print(f"(skipped writing JSON report: {exc})")
    raise SystemExit(0 if idempotent else 1)


if __name__ == "__main__":
    main()
