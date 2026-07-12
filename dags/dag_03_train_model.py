from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

_logger = logging.getLogger(__name__)

TRAIN_COMMAND = (
    "python -m scripts.train_model "
    "--feature-version '{{ params.feature_version }}' "
    "--validation-days {{ params.validation_days }} "
    "--random-seed {{ params.random_seed }} "
    "--output-path /tmp/evaluation_sprint_04.json"
)


def _notify_discord(context: dict, status: str) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        _logger.warning("DISCORD_WEBHOOK_URL not set — skipping Discord notification.")
        return

    dag_run = context.get("dag_run")
    dag_id = getattr(dag_run, "dag_id", "dag_03_train_model")
    run_id = getattr(dag_run, "run_id", "")
    conf = getattr(dag_run, "conf", {}) or {}
    reason = conf.get("reason", "n/a")
    note = getattr(dag_run, "note", None) or ""

    color = {"success": 0x2ECC71, "failed": 0xE74C3C}.get(status, 0x95A5A6)
    emoji = {"success": "✅", "failed": "❌"}.get(status, "ℹ️")

    payload = {
        "username": "HBAAC Retrain Bot",
        "embeds": [
            {
                "title": f"{emoji} Retrain {status.upper()} — {dag_id}",
                "color": color,
                "fields": [
                    {"name": "Run ID", "value": f"`{run_id}`", "inline": False},
                    {"name": "Reason", "value": str(reason), "inline": True},
                    {"name": "Status", "value": status, "inline": True},
                ],
                "footer": {"text": note or "HBAAC Airflow"},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        ],
    }
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "HBAAC-Airflow/1.0 (+retrain-notifier)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _logger.info("Discord notify %s -> HTTP %s", status, resp.status)
    except Exception:
        _logger.exception("Failed to post Discord notification (%s)", status)


def _on_success(context: dict) -> None:
    _notify_discord(context, "success")


def _on_failure(context: dict) -> None:
    _notify_discord(context, "failed")


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
    on_success_callback=_on_success,
    on_failure_callback=_on_failure,
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
