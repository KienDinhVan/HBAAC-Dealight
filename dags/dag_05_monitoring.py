from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

MONITOR_COMMAND = (
    "cd /opt/project && "
    "python -m scripts.run_monitoring "
    "--report-id 'sprint-07-dag-{{ ds_nodash }}' "
    "--feature-version '{{ params.feature_version }}' "
    "--output-dir /opt/project/data/monitoring "
    "--schema-file /opt/project/scripts/sprint_07_monitoring_schema.sql"
)

with DAG(
    dag_id="dag_05_monitoring",
    start_date=datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False,
    params={"feature_version": "sprint-03-v1-top100-a60-h56"},
    tags=["monitoring", "drift", "sprint-07"],
) as dag:
    check_latest_forecast_run = EmptyOperator(task_id="check_latest_forecast_run")
    compute_forecast_statistics = EmptyOperator(
        task_id="compute_forecast_statistics"
    )
    compute_actual_vs_prediction_if_available = EmptyOperator(
        task_id="compute_actual_vs_prediction_if_available"
    )
    run_evidently_drift_report = BashOperator(
        task_id="run_evidently_drift_report", bash_command=MONITOR_COMMAND
    )
    save_monitoring_report = EmptyOperator(task_id="save_monitoring_report")
    send_alert_if_needed = EmptyOperator(task_id="send_alert_if_needed")

    (
        check_latest_forecast_run
        >> compute_forecast_statistics
        >> compute_actual_vs_prediction_if_available
        >> run_evidently_drift_report
        >> save_monitoring_report
        >> send_alert_if_needed
    )
