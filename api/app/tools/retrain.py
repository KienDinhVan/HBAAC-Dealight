from __future__ import annotations

import json
import logging
from typing import Any

from api.app.clients.airflow import AirflowClient
from .base import Tool, tool

_logger = logging.getLogger(__name__)

TRAIN_DAG_ID = "train_hbaac_sku"
BATCH_DAG_ID = "forecast_hbaac_sku"


def make_retrain_tools(airflow: AirflowClient) -> list[Tool]:

    @tool(requires_approval=True)
    async def trigger_retrain(reason: str, feature_version: str = "sprint-03-v1-top100-a60-h56") -> str:
        """Trigger an Airflow run of the training DAG. Requires explicit user approval.

        Args:
            reason: Short description of why retrain is requested (drift, new data, etc.).
            feature_version: Feature registry version to use (default sprint-03-v1-top100-a60-h56).
        Returns:
            JSON string with dag_run_id and current state.
        """
        try:
            result = await airflow.trigger_dag(
                TRAIN_DAG_ID,
                conf={"feature_version": feature_version, "reason": reason},
                note=f"Retrain: {reason}",
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("trigger_retrain failed")
            return json.dumps({"ok": False, "error": str(exc)})
        return json.dumps(
            {"ok": True, "dag_id": TRAIN_DAG_ID, "dag_run_id": result.get("dag_run_id"), "state": result.get("state")}
        )

    @tool
    async def get_retrain_status(dag_run_id: str) -> str:
        """Get the current state of a retrain DAG run.

        Args:
            dag_run_id: The dag_run_id returned by trigger_retrain.
        Returns:
            JSON with state, start/end timestamps, note.
        """
        try:
            result = await airflow.get_dag_run(TRAIN_DAG_ID, dag_run_id)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})
        return json.dumps({"ok": True, **_simplify(result)})

    @tool
    async def list_recent_retrain_runs(limit: int = 5) -> str:
        """List recent retrain DAG runs ordered by execution date desc.

        Args:
            limit: Maximum number of runs to return (default 5, max 20).
        Returns:
            JSON-encoded list of dag run summaries.
        """
        limit_int = max(1, min(int(limit), 20))
        try:
            runs = await airflow.list_dag_runs(TRAIN_DAG_ID, limit=limit_int)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})
        return json.dumps([_simplify(r) for r in runs])

    return [trigger_retrain, get_retrain_status, list_recent_retrain_runs]


def _simplify(dag_run: dict[str, Any]) -> dict[str, Any]:
    keep = {"dag_run_id", "dag_id", "state", "execution_date", "start_date", "end_date", "run_type", "note"}
    return {k: dag_run.get(k) for k in keep if k in dag_run}
