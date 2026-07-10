from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

_logger = logging.getLogger(__name__)


class AirflowClient:
    """Thin async client for the Airflow stable REST API (basic auth)."""

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._timeout = timeout

    async def trigger_dag(
        self,
        dag_id: str,
        conf: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        run_id = f"manual__{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        payload: dict[str, Any] = {
            "dag_run_id": run_id,
            "conf": conf or {},
        }
        if note:
            payload["note"] = note
        url = f"{self._base_url}/api/v1/dags/{dag_id}/dagRuns"
        async with httpx.AsyncClient(timeout=self._timeout, auth=self._auth) as client:
            response = await client.post(url, json=payload)
            if response.is_error:
                _logger.error("Airflow trigger failed %s — %s", response.status_code, response.text)
                response.raise_for_status()
            return response.json()

    async def get_dag_run(self, dag_id: str, dag_run_id: str) -> dict[str, Any]:
        url = f"{self._base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
        async with httpx.AsyncClient(timeout=self._timeout, auth=self._auth) as client:
            response = await client.get(url)
            if response.is_error:
                _logger.error("Airflow get_dag_run failed %s — %s", response.status_code, response.text)
                response.raise_for_status()
            return response.json()

    async def list_task_instances(self, dag_id: str, dag_run_id: str) -> list[dict[str, Any]]:
        url = f"{self._base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
        async with httpx.AsyncClient(timeout=self._timeout, auth=self._auth) as client:
            response = await client.get(url)
            if response.is_error:
                _logger.error(
                    "Airflow list_task_instances failed %s — %s",
                    response.status_code,
                    response.text,
                )
                response.raise_for_status()
            return response.json().get("task_instances", [])

    async def list_dag_runs(
        self,
        dag_id: str,
        limit: int = 10,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "order_by": "-execution_date"}
        if state:
            params["state"] = state
        url = f"{self._base_url}/api/v1/dags/{dag_id}/dagRuns"
        async with httpx.AsyncClient(timeout=self._timeout, auth=self._auth) as client:
            response = await client.get(url, params=params)
            if response.is_error:
                _logger.error("Airflow list_dag_runs failed %s — %s", response.status_code, response.text)
                response.raise_for_status()
            return response.json().get("dag_runs", [])
