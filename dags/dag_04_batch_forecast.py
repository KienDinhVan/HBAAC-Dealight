from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

FORECAST_COMMAND = (
    "cd /opt/project && "
    "python -m scripts.run_batch_forecast "
    "--model-name '{{ params.model_name }}' "
    "--feature-version '{{ params.feature_version }}' "
    "--forecast-date '{{ params.forecast_date }}' "
    "--run-id 'sprint-05-dag-{{ ds_nodash }}' "
    "--max-horizon {{ params.max_horizon }} "
    "--lookback-days {{ params.lookback_days }} "
    "--sku-lookback-days {{ params.sku_lookback_days }} "
    "--schema-file /opt/project/scripts/sprint_05_serving_schema.sql"
)

with DAG(
    dag_id="dag_04_batch_forecast",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    params={
        "model_name": "sku-demand-lightgbm",
        "feature_version": "sprint-03-v1-top100-a60-h56",
        "forecast_date": "2025-09-05",
        "max_horizon": 56,
        "lookback_days": 200,
        "sku_lookback_days": 0,
    },
    tags=["forecasting", "serving", "sprint-05"],
) as dag:
    get_latest_model = EmptyOperator(task_id="get_latest_model")
    build_inference_features = EmptyOperator(task_id="build_inference_features")
    validate_inference_features = EmptyOperator(
        task_id="validate_inference_features"
    )
    generate_predictions = BashOperator(
        task_id="generate_predictions", bash_command=FORECAST_COMMAND
    )
    validate_predictions = EmptyOperator(task_id="validate_predictions")
    save_forecast = EmptyOperator(task_id="save_forecast")
    mark_latest_forecast_run = EmptyOperator(task_id="mark_latest_forecast_run")

    (
        get_latest_model
        >> build_inference_features
        >> validate_inference_features
        >> generate_predictions
        >> validate_predictions
        >> save_forecast
        >> mark_latest_forecast_run
    )
