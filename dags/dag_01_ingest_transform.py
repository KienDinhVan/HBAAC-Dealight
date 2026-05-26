from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

PIPELINE_COMMAND = (
    "python -m scripts.run_data_pipeline "
    "--source-path /opt/project/data/raw/train.csv "
    "--schema-path /opt/project/scripts/sprint_02_pipeline_schema.sql "
    "--batch-id '{{ params.batch_id }}' "
)


def pipeline_task(task_id: str, stage: str) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=PIPELINE_COMMAND + f"--stage {stage}",
    )


with DAG(
    dag_id="dag_01_ingest_transform",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    params={"batch_id": "train-full"},
    tags=["pipeline", "sprint-02"],
) as dag:
    validate_raw_file = pipeline_task("validate_raw_file", "validate")
    ingest_raw = pipeline_task("ingest_raw", "raw")
    build_bronze = pipeline_task("build_bronze", "bronze")
    build_silver = pipeline_task("build_silver", "silver")
    build_gold = pipeline_task("build_gold", "gold")
    run_data_quality_checks = pipeline_task("run_data_quality_checks", "quality")

    (
        validate_raw_file
        >> ingest_raw
        >> build_bronze
        >> build_silver
        >> build_gold
        >> run_data_quality_checks
    )
