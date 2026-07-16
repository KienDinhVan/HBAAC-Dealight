"""Stage runner for the DE GCS pipeline (DE_arch.png).

Each --stage moves one batch through the GCS layers:
raw -> staging (+quarantine) -> curated -> offline_store (BigQuery).
All google.cloud imports are lazy so unit tests run without GCP credentials.
"""
from __future__ import annotations

import argparse
import io
import json
import os

import pandas as pd
import requests

from hbacc_prj.de_pipeline import build_curated, validate_transactions

RAW_BLOB = "raw/batch_id={batch_id}/train.csv"
STAGING_BLOB = "staging/batch_id={batch_id}/transactions.parquet"
QUARANTINE_BLOB = "quarantine/batch_id={batch_id}/rejects.csv"
CURATED_BLOB = "curated/batch_id={batch_id}/sales_daily.parquet"
DQ_SUMMARY_BLOB = "dq/batch_id={batch_id}/summary.json"

BQ_TABLE_NAME = "sales_daily"


def _post_discord(webhook_url: str, content: str) -> None:
    response = requests.post(
        webhook_url,
        json={"content": content},
        headers={"User-Agent": "Dealight-MLOps/1.0"},
        timeout=10,
    )
    response.raise_for_status()


def _report_data_quality(bucket, batch_id: str, summary: dict) -> None:
    """Persist the DQ summary and alert when the reject ratio breaches the threshold."""
    bucket.blob(DQ_SUMMARY_BLOB.format(batch_id=batch_id)).upload_from_string(
        json.dumps(summary), content_type="application/json"
    )
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    threshold = float(os.environ.get("DQ_REJECT_ALERT_RATIO", "0.1"))
    if webhook_url and summary["reject_ratio"] > threshold:
        message = (
            f"[DE pipeline] batch {batch_id}: "
            f"{summary['rows_rejected']}/{summary['rows_in']} rows rejected "
            f"({summary['reject_ratio']:.1%}, threshold {threshold:.0%}) — "
            f"reasons: {summary['reject_reasons']}. See quarantine/batch_id={batch_id}/."
        )
        try:
            _post_discord(webhook_url, message)
        except Exception as exc:  # noqa: BLE001 — alerting must never fail the batch
            print(f"WARNING: Discord alert failed: {exc}")


def stage_raw(bucket, batch_id: str, source_blob: str) -> dict:
    source = bucket.blob(source_blob)
    if not source_blob or not source.exists():
        raise FileNotFoundError(f"Source blob not found: {source_blob!r}")
    raw_name = RAW_BLOB.format(batch_id=batch_id)
    bucket.copy_blob(source, bucket, raw_name)
    return {"raw_blob": raw_name}


def stage_staging(bucket, batch_id: str) -> dict:
    raw_bytes = bucket.blob(RAW_BLOB.format(batch_id=batch_id)).download_as_bytes()
    df = pd.read_csv(io.BytesIO(raw_bytes))
    result = validate_transactions(df)

    if len(result.rejected) > 0:
        bucket.blob(QUARANTINE_BLOB.format(batch_id=batch_id)).upload_from_string(
            result.rejected.to_csv(index=False), content_type="text/csv"
        )

    summary = {
        "rows_in": int(len(df)),
        "rows_passed": int(len(result.passed)),
        "rows_rejected": int(len(result.rejected)),
        "reject_ratio": round(len(result.rejected) / len(df), 4) if len(df) else 0.0,
        "reject_reasons": (
            result.rejected["reject_reason"].value_counts().to_dict()
            if len(result.rejected)
            else {}
        ),
    }
    # Report before the all-rejected failure so a bad batch still alerts.
    _report_data_quality(bucket, batch_id, summary)

    if len(result.passed) == 0:
        raise ValueError(f"All rows rejected for batch {batch_id} — see quarantine/")

    buffer = io.BytesIO()
    result.passed.to_parquet(buffer, index=False)
    bucket.blob(STAGING_BLOB.format(batch_id=batch_id)).upload_from_string(
        buffer.getvalue(), content_type="application/octet-stream"
    )
    return summary


def stage_curated(bucket, batch_id: str) -> dict:
    staging_bytes = bucket.blob(STAGING_BLOB.format(batch_id=batch_id)).download_as_bytes()
    staging = pd.read_parquet(io.BytesIO(staging_bytes))
    curated = build_curated(staging, batch_id=batch_id)
    buffer = io.BytesIO()
    curated.to_parquet(buffer, index=False)
    bucket.blob(CURATED_BLOB.format(batch_id=batch_id)).upload_from_string(
        buffer.getvalue(), content_type="application/octet-stream"
    )
    return {"rows": int(len(curated))}


def stage_offline_store(
    batch_id: str,
    *,
    project: str,
    dataset: str,
    bucket_name: str,
    biglake_connection: str = "",
    location: str = "asia-southeast1",
    bq_client=None,
) -> dict:
    from google.cloud import bigquery

    client = bq_client or bigquery.Client(project=project)
    table_id = f"{project}.{dataset}.{BQ_TABLE_NAME}"
    schema = [
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("item_code", "STRING"),
        bigquery.SchemaField("total_quantity", "FLOAT64"),
        bigquery.SchemaField("total_sales", "FLOAT64"),
        bigquery.SchemaField("total_cost", "FLOAT64"),
        bigquery.SchemaField("txn_count", "INT64"),
        bigquery.SchemaField("batch_id", "STRING"),
        bigquery.SchemaField("loaded_at", "TIMESTAMP"),
    ]
    if biglake_connection:
        # BigQuery-managed Iceberg table: data + metadata live in GCS under
        # warehouse/, written through the BigLake connection's service
        # account. Iceberg tables support clustering but not time
        # partitioning, and must be created via DDL.
        column_defs = ", ".join(f"{field.name} {field.field_type}" for field in schema)
        ddl = (
            f"CREATE TABLE IF NOT EXISTS `{table_id}` ({column_defs}) "
            f"CLUSTER BY date "
            f"WITH CONNECTION `{project}.{location}.{biglake_connection}` "
            f"OPTIONS (file_format = 'PARQUET', table_format = 'ICEBERG', "
            f"storage_uri = 'gs://{bucket_name}/warehouse/{BQ_TABLE_NAME}')"
        )
        client.query(ddl).result()
    else:
        table = bigquery.Table(table_id, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(field="date")
        client.create_table(table, exists_ok=True)

    # Load the batch into a temporary table, then swap it in with one atomic
    # MERGE: dates present in the batch are fully replaced (partition
    # overwrite), so re-uploading a corrected file doubles as backfill and
    # duplicate uploads cannot double-count.
    temp_table_id = f"{table_id}__load_{batch_id}"
    uri = f"gs://{bucket_name}/" + CURATED_BLOB.format(batch_id=batch_id)
    load_job = client.load_table_from_uri(
        uri,
        temp_table_id,
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=schema,
        ),
    )
    load_job.result()

    # BigQuery rejects subqueries in MERGE WHEN clauses, so the swap is a
    # multi-statement transaction instead (equally atomic).
    columns = ", ".join(field.name for field in schema)
    swap_sql = (
        f"BEGIN TRANSACTION; "
        f"DELETE FROM `{table_id}` "
        f"WHERE date IN (SELECT DISTINCT date FROM `{temp_table_id}`); "
        f"INSERT INTO `{table_id}` ({columns}) "
        f"SELECT {columns} FROM `{temp_table_id}`; "
        f"COMMIT TRANSACTION;"
    )
    client.query(swap_sql).result()
    client.delete_table(temp_table_id, not_found_ok=True)
    return {"table": table_id, "loaded_rows": int(load_job.output_rows or 0)}


ONLINE_STORE_KEY = "sales_daily:{item_code}"


def stage_online_store(bucket, batch_id: str, *, redis_client=None) -> dict:
    """Sync the latest curated row per item into Redis for low-latency serving."""
    curated_bytes = bucket.blob(CURATED_BLOB.format(batch_id=batch_id)).download_as_bytes()
    curated = pd.read_parquet(io.BytesIO(curated_bytes))
    latest = curated.sort_values("date").groupby("item_code", as_index=False).tail(1)

    if redis_client is None:
        import redis

        redis_client = redis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        )
    pipe = redis_client.pipeline()
    for row in latest.itertuples(index=False):
        pipe.hset(
            ONLINE_STORE_KEY.format(item_code=row.item_code),
            mapping={
                "date": str(row.date),
                "total_quantity": float(row.total_quantity),
                "total_sales": float(row.total_sales),
                "total_cost": float(row.total_cost),
                "txn_count": int(row.txn_count),
                "batch_id": batch_id,
            },
        )
    pipe.execute()
    return {"items_synced": int(len(latest))}


def _gcs_bucket(bucket_name: str, project: str):
    from google.cloud import storage

    return storage.Client(project=project or None).bucket(bucket_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one DE GCS pipeline stage.")
    parser.add_argument(
        "--stage",
        required=True,
        choices=["raw", "staging", "curated", "offline_store", "online_store"],
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--source-blob", default="")
    parser.add_argument("--bucket", default=os.environ.get("GCS_BUCKET", ""))
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", ""))
    parser.add_argument("--dataset", default=os.environ.get("BQ_DATASET", "dealight"))
    args = parser.parse_args()

    if not args.bucket:
        raise SystemExit("GCS_BUCKET (or --bucket) is required")

    if args.stage == "offline_store":
        if not args.project:
            raise SystemExit("GCP_PROJECT_ID (or --project) is required for offline_store")
        summary = stage_offline_store(
            args.batch_id,
            project=args.project,
            dataset=args.dataset,
            bucket_name=args.bucket,
            biglake_connection=os.environ.get("BQ_BIGLAKE_CONNECTION", ""),
            location=os.environ.get("BQ_LOCATION", "asia-southeast1"),
        )
    else:
        bucket = _gcs_bucket(args.bucket, args.project)
        if args.stage == "raw":
            summary = stage_raw(bucket, args.batch_id, args.source_blob)
        elif args.stage == "staging":
            summary = stage_staging(bucket, args.batch_id)
        elif args.stage == "online_store":
            summary = stage_online_store(bucket, args.batch_id)
        else:
            summary = stage_curated(bucket, args.batch_id)

    print(json.dumps({"stage": args.stage, "batch_id": args.batch_id, **summary}, default=str))


if __name__ == "__main__":
    main()
