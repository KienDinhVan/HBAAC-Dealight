from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool

from api.app.clients.airflow import AirflowClient
from api.app.clients.bigquery import OfflineStoreClient
from api.app.clients.gcs import GcsUploader
from api.app.clients.redis_store import OnlineStoreClient
from api.app.config import get_settings
from api.app.deps import (
    get_airflow_client,
    get_gcs_uploader,
    get_offline_store,
    get_online_store,
)
from api.app.schemas import (
    IngestBatchItem,
    IngestBatchListResponse,
    IngestDqDetailResponse,
    IngestRunStatusResponse,
    IngestRunTasksResponse,
    IngestTaskState,
    IngestUploadResponse,
    OfflineStoreStatsResponse,
    OnlineStoreItemResponse,
)

INGEST_DAG_ID = "dag_07_de_gcs_pipeline"

# Blob layout mirrors scripts/run_de_pipeline.py.
DQ_SUMMARY_BLOB = "dq/batch_id={batch_id}/summary.json"
QUARANTINE_BLOB = "quarantine/batch_id={batch_id}/rejects.csv"
_DQ_BLOB_RE = re.compile(r"^dq/batch_id=([0-9a-f]{32})/summary\.json$")
_BATCH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_ITEM_CODE_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

PIPELINE_TASK_ORDER = [
    "ingest_raw",
    "process_validate_to_staging",
    "build_curated",
    "load_offline_store",
    "sync_online_store",
]
QUARANTINE_PREVIEW_ROWS = 20
QUARANTINE_PREVIEW_BYTES = 65_536


def _is_not_found(exc: Exception) -> bool:
    return exc.__class__.__name__ == "NotFound"

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


@router.get("/runs/{dag_run_id}/tasks", response_model=IngestRunTasksResponse)
async def run_tasks(
    dag_run_id: str,
    airflow: AirflowClient = Depends(get_airflow_client),
) -> IngestRunTasksResponse:
    try:
        run = await airflow.get_dag_run(INGEST_DAG_ID, dag_run_id)
        instances = await airflow.list_task_instances(INGEST_DAG_ID, dag_run_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Airflow query failed: {exc}"
        ) from exc
    order = {task_id: i for i, task_id in enumerate(PIPELINE_TASK_ORDER)}
    instances.sort(key=lambda t: order.get(t.get("task_id", ""), len(order)))
    return IngestRunTasksResponse(
        dag_id=INGEST_DAG_ID,
        dag_run_id=dag_run_id,
        state=run.get("state"),
        tasks=[
            IngestTaskState(
                task_id=t.get("task_id", ""),
                state=t.get("state"),
                start_date=t.get("start_date"),
                end_date=t.get("end_date"),
            )
            for t in instances
        ],
    )


@router.get("/batches", response_model=IngestBatchListResponse)
async def list_batches(
    limit: int = Query(default=20, ge=1, le=100),
    gcs: GcsUploader | None = Depends(get_gcs_uploader),
) -> IngestBatchListResponse:
    if gcs is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "GCS is not configured"
        )
    try:
        blobs = await run_in_threadpool(gcs.list_blobs, "dq/")
        entries = []
        for blob in blobs:
            match = _DQ_BLOB_RE.match(blob.name)
            if match:
                entries.append((match.group(1), blob))
        entries.sort(
            key=lambda e: e[1].time_created.timestamp() if e[1].time_created else 0.0,
            reverse=True,
        )
        items = []
        for batch_id, blob in entries[:limit]:
            summary = json.loads(await run_in_threadpool(gcs.download_bytes, blob.name))
            items.append(
                IngestBatchItem(
                    batch_id=batch_id,
                    created_at=blob.time_created,
                    rows_in=int(summary.get("rows_in", 0)),
                    rows_passed=int(summary.get("rows_passed", 0)),
                    rows_rejected=int(summary.get("rows_rejected", 0)),
                    reject_ratio=float(summary.get("reject_ratio", 0.0)),
                )
            )
    except Exception as exc:  # noqa: BLE001
        _logger.exception("GCS batch listing failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"GCS listing failed: {exc}"
        ) from exc
    return IngestBatchListResponse(items=items)


@router.get("/batches/{batch_id}/dq", response_model=IngestDqDetailResponse)
async def batch_dq(
    batch_id: str,
    gcs: GcsUploader | None = Depends(get_gcs_uploader),
) -> IngestDqDetailResponse:
    if not _BATCH_ID_RE.match(batch_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid batch id")
    if gcs is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "GCS is not configured"
        )
    try:
        raw_summary = await run_in_threadpool(
            gcs.download_bytes, DQ_SUMMARY_BLOB.format(batch_id=batch_id)
        )
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No DQ summary for batch {batch_id}"
            ) from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"GCS read failed: {exc}"
        ) from exc
    summary = json.loads(raw_summary)

    preview: list[dict] = []
    if int(summary.get("rows_rejected", 0)) > 0:
        try:
            raw = await run_in_threadpool(
                gcs.download_bytes,
                QUARANTINE_BLOB.format(batch_id=batch_id),
                0,
                QUARANTINE_PREVIEW_BYTES,
            )
            lines = raw.decode("utf-8", errors="replace").splitlines()
            # A ranged read can cut the last line mid-row — drop it.
            if len(raw) >= QUARANTINE_PREVIEW_BYTES and len(lines) > 1:
                lines = lines[:-1]
            reader = csv.DictReader(io.StringIO("\n".join(lines)))
            for i, row in enumerate(reader):
                if i >= QUARANTINE_PREVIEW_ROWS:
                    break
                preview.append(row)
        except Exception:  # noqa: BLE001 — preview is best-effort
            _logger.exception("Quarantine preview failed for batch %s", batch_id)
    return IngestDqDetailResponse(
        batch_id=batch_id,
        summary=summary,
        quarantine_preview=preview,
        preview_truncated=int(summary.get("rows_rejected", 0)) > len(preview),
    )


@router.get("/offline-store/stats", response_model=OfflineStoreStatsResponse)
async def offline_store_stats(
    as_of: datetime | None = Query(default=None),
    store: OfflineStoreClient | None = Depends(get_offline_store),
) -> OfflineStoreStatsResponse:
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "BigQuery offline store is not configured (set GCP_PROJECT_ID)",
        )
    try:
        rows = await run_in_threadpool(store.batch_stats, as_of)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("BigQuery stats query failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"BigQuery query failed: {exc}"
        ) from exc
    return OfflineStoreStatsResponse(
        as_of=as_of,
        total_rows=sum(int(r.get("row_count", 0)) for r in rows),
        batches=rows,
    )


@router.get("/online-store/{item_code}", response_model=OnlineStoreItemResponse)
async def online_store_item(
    item_code: str,
    store: OnlineStoreClient | None = Depends(get_online_store),
) -> OnlineStoreItemResponse:
    if not _ITEM_CODE_RE.match(item_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid item code")
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Redis online store is not configured (set REDIS_URL)",
        )
    try:
        record = await run_in_threadpool(store.get_item, item_code)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Redis lookup failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Redis lookup failed: {exc}"
        ) from exc
    return OnlineStoreItemResponse(
        item_code=item_code, found=record is not None, record=record
    )
