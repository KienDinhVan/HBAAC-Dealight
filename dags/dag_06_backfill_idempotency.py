from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

# Backfill idempotency flow (evidence E6) as a native Airflow DAG.
#
# Re-runs the full ingest -> bronze -> silver -> gold pipeline TWICE for the
# same batch_id, persisting a per-layer fingerprint (row-count + md5 checksum)
# after each pass, then compares the two fingerprints. Because every pipeline
# stage does DELETE ... WHERE batch_id before re-loading, both passes MUST
# produce identical row-counts and checksums -> proving the backfill is
# idempotent. The compare task fails (non-zero exit) if anything diverges.

SOURCE = "/opt/project/data/raw/train.csv"
SCHEMA = "/opt/project/scripts/sprint_02_pipeline_schema.sql"
FP_DIR = "/opt/project/data/monitoring"
FP_FIRST = f"{FP_DIR}/backfill_fp_first.json"
FP_SECOND = f"{FP_DIR}/backfill_fp_second.json"

RUN_CMD = (
    "python -m scripts.run_backfill_idempotency --mode run "
    "--batch-id '{{ params.batch_id }}' "
    f"--source-path {SOURCE} --schema-path {SCHEMA} "
)


with DAG(
    dag_id="dag_06_backfill_idempotency",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    params={"batch_id": "backfill-demo"},
    tags=["backfill", "idempotency", "data-quality", "sprint-02"],
) as dag:
    backfill_first_run = BashOperator(
        task_id="backfill_first_run",
        bash_command=RUN_CMD + f"--run-label first --fingerprint-out {FP_FIRST}",
    )
    backfill_second_run = BashOperator(
        task_id="backfill_second_run",
        bash_command=RUN_CMD + f"--run-label second --fingerprint-out {FP_SECOND}",
    )
    compare_idempotency = BashOperator(
        task_id="compare_idempotency",
        bash_command=(
            "python -m scripts.run_backfill_idempotency --mode compare "
            f"--fingerprint-in {FP_FIRST} {FP_SECOND}"
        ),
    )

    backfill_first_run >> backfill_second_run >> compare_idempotency
