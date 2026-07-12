from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.app.clients.airflow import AirflowClient
from api.app.deps import get_airflow_client
from api.app.schemas import (
    RetrainRunStatusResponse,
    RetrainTriggerRequest,
    RetrainTriggerResponse,
)
from api.app.tools.retrain import TRAIN_DAG_ID

router = APIRouter(prefix="/retrain", tags=["retrain"])
_logger = logging.getLogger(__name__)


@router.post("/trigger", response_model=RetrainTriggerResponse)
async def trigger(
    body: RetrainTriggerRequest,
    airflow: AirflowClient = Depends(get_airflow_client),
) -> RetrainTriggerResponse:
    conf: dict[str, str] = {"reason": body.reason}
    if body.feature_version:
        conf["feature_version"] = body.feature_version
    try:
        result = await airflow.trigger_dag(
            TRAIN_DAG_ID, conf=conf, note=f"Retrain: {body.reason}"
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Retrain trigger failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Airflow trigger failed: {exc}") from exc
    return RetrainTriggerResponse(
        dag_id=TRAIN_DAG_ID,
        dag_run_id=result.get("dag_run_id", ""),
        state=result.get("state"),
        note=result.get("note"),
    )


@router.get("/runs/{dag_run_id}", response_model=RetrainRunStatusResponse)
async def status_run(
    dag_run_id: str,
    airflow: AirflowClient = Depends(get_airflow_client),
) -> RetrainRunStatusResponse:
    try:
        run = await airflow.get_dag_run(TRAIN_DAG_ID, dag_run_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Airflow query failed: {exc}") from exc
    return RetrainRunStatusResponse(
        dag_id=TRAIN_DAG_ID,
        dag_run_id=run.get("dag_run_id", dag_run_id),
        state=run.get("state"),
        execution_date=run.get("execution_date"),
        start_date=run.get("start_date"),
        end_date=run.get("end_date"),
        note=run.get("note"),
    )
