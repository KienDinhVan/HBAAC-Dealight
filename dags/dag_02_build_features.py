from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

FEATURE_COMMAND = (
    "python -m scripts.run_feature_pipeline "
    "--schema-path /opt/project/scripts/sprint_03_feature_schema.sql "
    "--feature-version '{{ params.feature_version }}' "
    "--max-skus {{ params.max_skus }} "
    "--as-of-days {{ params.as_of_days }} "
    "--max-horizon 56 "
)


def feature_task(task_id: str, stage: str) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=FEATURE_COMMAND + f"--stage {stage}",
    )


with DAG(
    dag_id="dag_02_build_features",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    params={
        "feature_version": "sprint-03-v1-top100-a60-h56",
        "max_skus": 100,
        "as_of_days": 60,
    },
    tags=["features", "sprint-03"],
) as dag:
    load_gold_data = feature_task("load_gold_data", "validate-gold")
    generate_base_calendar = EmptyOperator(task_id="generate_base_calendar")
    compute_lag_features = EmptyOperator(task_id="compute_lag_features")
    compute_rolling_features = EmptyOperator(task_id="compute_rolling_features")
    compute_sku_features = EmptyOperator(task_id="compute_sku_features")
    build_training_frame = feature_task("build_training_frame", "save")
    validate_features = feature_task("validate_features", "quality")
    save_offline_features = feature_task("save_offline_features", "summary")

    (
        load_gold_data
        >> generate_base_calendar
        >> compute_lag_features
        >> compute_rolling_features
        >> compute_sku_features
        >> build_training_frame
        >> validate_features
        >> save_offline_features
    )
