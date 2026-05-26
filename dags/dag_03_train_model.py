from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

TRAIN_COMMAND = (
    "python -m scripts.train_model "
    "--feature-version '{{ params.feature_version }}' "
    "--validation-days {{ params.validation_days }} "
    "--random-seed {{ params.random_seed }} "
    "--output-path /tmp/evaluation_sprint_04.json"
)

with DAG(
    dag_id="dag_03_train_model",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    params={
        "feature_version": "sprint-03-v1-top100-a60-h56",
        "validation_days": 28,
        "random_seed": 2026,
    },
    tags=["training", "mlflow", "sprint-04"],
) as dag:
    load_features = EmptyOperator(task_id="load_features")
    split_train_validation = EmptyOperator(task_id="split_train_validation")
    train_baselines = EmptyOperator(task_id="train_baselines")
    train_lightgbm = BashOperator(task_id="train_lightgbm", bash_command=TRAIN_COMMAND)
    evaluate_model = EmptyOperator(task_id="evaluate_model")
    compare_with_current_production = EmptyOperator(
        task_id="compare_with_current_production"
    )
    log_to_mlflow = EmptyOperator(task_id="log_to_mlflow")
    register_model_if_pass = EmptyOperator(task_id="register_model_if_pass")

    (
        load_features
        >> split_train_validation
        >> train_baselines
        >> train_lightgbm
        >> evaluate_model
        >> compare_with_current_production
        >> log_to_mlflow
        >> register_model_if_pass
    )
