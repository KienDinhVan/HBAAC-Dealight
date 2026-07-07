from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from api.app.clients.airflow import AirflowClient
from api.app.clients.gcs import GcsUploader
from api.app.config import get_settings
from api.app.deps import get_airflow_client, get_gcs_uploader
from api.app.schemas import IngestRunStatusResponse, IngestUploadResponse

INGEST_DAG_ID = "dag_07_de_gcs_pipeline"

router = APIRouter(prefix="/ingest", tags=["ingest"])
_logger = logging.getLogger(__name__)


@router.post("/upload", response_model=IngestUploadResponse)
async def upload(
    file: UploadFile = File(...),
    airflow: AirflowClient = Depends(get_airflow_client),
    gcs: GcsUploader | None = Depends(get_gcs_uploader),
) -> IngestUploadResponse:
    if gcs is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "GCS is not configured (set GCS_BUCKET / GOOGLE_APPLICATION_CREDENTIALS)",
        )
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .csv files are accepted")
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")
    max_bytes = get_settings().max_upload_bytes
    if len(data) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {max_bytes} byte upload limit",
        )

    batch_id = uuid4().hex
    source_blob = f"landing/{batch_id}/{filename}"
    try:
        source_uri = await run_in_threadpool(gcs.upload_bytes, source_blob, data)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("GCS landing upload failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"GCS upload failed: {exc}"
        ) from exc

    try:
        result = await airflow.trigger_dag(
            INGEST_DAG_ID,
            conf={"batch_id": batch_id, "source_blob": source_blob},
            note=f"CSV upload: {filename}",
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Airflow trigger failed after upload to %s", source_uri)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"File uploaded to {source_uri} but Airflow trigger failed: {exc}",
        ) from exc

    return IngestUploadResponse(
        batch_id=batch_id,
        source_uri=source_uri,
        dag_id=INGEST_DAG_ID,
        dag_run_id=result.get("dag_run_id", ""),
        state=result.get("state"),
    )


@router.get("/runs/{dag_run_id}", response_model=IngestRunStatusResponse)
async def run_status(
    dag_run_id: str,
    airflow: AirflowClient = Depends(get_airflow_client),
) -> IngestRunStatusResponse:
    try:
        run = await airflow.get_dag_run(INGEST_DAG_ID, dag_run_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Airflow query failed: {exc}"
        ) from exc
    return IngestRunStatusResponse(
        dag_id=INGEST_DAG_ID,
        dag_run_id=run.get("dag_run_id", dag_run_id),
        state=run.get("state"),
        execution_date=run.get("execution_date"),
        start_date=run.get("start_date"),
        end_date=run.get("end_date"),
        note=run.get("note"),
    )
