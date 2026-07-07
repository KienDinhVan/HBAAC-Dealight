from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

PIPELINE_COMMAND = (
    "python -m scripts.run_de_pipeline "
    "--batch-id '{{ dag_run.conf[\"batch_id\"] }}' "
    "--source-blob '{{ dag_run.conf.get(\"source_blob\", \"\") }}' "
)


def pipeline_task(task_id: str, stage: str) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=PIPELINE_COMMAND + f"--stage {stage}",
        retries=1,
    )


with DAG(
    dag_id="dag_07_de_gcs_pipeline",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["pipeline", "de-arch", "gcp"],
) as dag:
    ingest_raw = pipeline_task("ingest_raw", "raw")
    process_validate_to_staging = pipeline_task("process_validate_to_staging", "staging")
    build_curated = pipeline_task("build_curated", "curated")
    load_offline_store = pipeline_task("load_offline_store", "offline_store")

    ingest_raw >> process_validate_to_staging >> build_curated >> load_offline_store
