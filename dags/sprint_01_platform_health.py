from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.empty import EmptyOperator


with DAG(
    dag_id="sprint_01_platform_health",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["foundation", "sprint-01"],
) as dag:
    platform_ready = EmptyOperator(task_id="platform_ready")
