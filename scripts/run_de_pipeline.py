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

from hbacc_prj.de_pipeline import build_curated, validate_transactions

RAW_BLOB = "raw/batch_id={batch_id}/train.csv"
STAGING_BLOB = "staging/batch_id={batch_id}/transactions.parquet"
QUARANTINE_BLOB = "quarantine/batch_id={batch_id}/rejects.csv"
CURATED_BLOB = "curated/batch_id={batch_id}/sales_daily.parquet"

BQ_TABLE_NAME = "sales_daily"


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
    if len(result.passed) == 0:
        raise ValueError(f"All rows rejected for batch {batch_id} — see quarantine/")

    buffer = io.BytesIO()
    result.passed.to_parquet(buffer, index=False)
    bucket.blob(STAGING_BLOB.format(batch_id=batch_id)).upload_from_string(
        buffer.getvalue(), content_type="application/octet-stream"
    )
    return {
        "rows_in": int(len(df)),
        "rows_passed": int(len(result.passed)),
        "rows_rejected": int(len(result.rejected)),
    }


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
    batch_id: str, *, project: str, dataset: str, bucket_name: str, bq_client=None
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
    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(field="date")
    client.create_table(table, exists_ok=True)

    client.query(
        f"DELETE FROM `{table_id}` WHERE batch_id = @batch_id",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("batch_id", "STRING", batch_id)
            ]
        ),
    ).result()

    uri = f"gs://{bucket_name}/" + CURATED_BLOB.format(batch_id=batch_id)
    load_job = client.load_table_from_uri(
        uri,
        table_id,
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        ),
    )
    load_job.result()
    return {"table": table_id, "loaded_rows": int(load_job.output_rows or 0)}


def _gcs_bucket(bucket_name: str, project: str):
    from google.cloud import storage

    return storage.Client(project=project or None).bucket(bucket_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one DE GCS pipeline stage.")
    parser.add_argument(
        "--stage",
        required=True,
        choices=["raw", "staging", "curated", "offline_store"],
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
        )
    else:
        bucket = _gcs_bucket(args.bucket, args.project)
        if args.stage == "raw":
            summary = stage_raw(bucket, args.batch_id, args.source_blob)
        elif args.stage == "staging":
            summary = stage_staging(bucket, args.batch_id)
        else:
            summary = stage_curated(bucket, args.batch_id)

    print(json.dumps({"stage": args.stage, "batch_id": args.batch_id, **summary}, default=str))


if __name__ == "__main__":
    main()
