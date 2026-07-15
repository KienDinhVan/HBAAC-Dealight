from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from api.app.clients.airflow import AirflowClient
from api.app.config import get_settings
from api.app.deps import get_airflow_client
from api.app.schemas import (
    PredictJobResponse,
    PredictJobStatusResponse,
    PredictPoint,
)

router = APIRouter(prefix="/predict", tags=["predict"])
_logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"Date", "ItemCode"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
FORECAST_DAG_ID = "forecast_hbaac_sku"

# In-memory job registry. Replaced by Postgres/Redis in production rollout.
_JOBS: dict[str, dict[str, Any]] = {}


@router.post("/csv", response_model=PredictJobResponse)
async def predict_csv(
    request: Request,
    file: UploadFile = File(...),
    dataset: str = Form("hbaac_sku"),
    airflow: AirflowClient = Depends(get_airflow_client),
) -> PredictJobResponse:
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "CSV exceeds 100 MB limit")
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"CSV parse error: {exc}") from exc

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"CSV missing required column(s): {sorted(missing)}",
        )

    rows = len(df)
    job_id = uuid.uuid4().hex
    settings = get_settings()

    if rows <= settings.inline_predict_max_rows:
        items, chart_spec = _predict_inline(df, request, dataset)
        _JOBS[job_id] = {
            "status": "completed",
            "mode": "inline",
            "items": [p.model_dump(mode="json") for p in items],
        }
        return PredictJobResponse(
            job_id=job_id,
            mode="inline",
            status="completed",
            rows=rows,
            items=items,
            chart_spec=chart_spec,
        )

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{job_id}.csv"
    target.write_bytes(raw)
    try:
        result = await airflow.trigger_dag(
            FORECAST_DAG_ID,
            conf={"csv_path": str(target), "run_id": job_id},
            note=f"Inline predict overflow ({rows} rows)",
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Failed to enqueue async predict job %s", job_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Airflow trigger failed: {exc}") from exc
    _JOBS[job_id] = {
        "status": "queued",
        "mode": "async",
        "dag_run_id": result.get("dag_run_id"),
    }
    return PredictJobResponse(
        job_id=job_id,
        mode="async",
        status="queued",
        rows=rows,
        dag_run_id=result.get("dag_run_id"),
    )


@router.get("/jobs/{job_id}", response_model=PredictJobStatusResponse)
async def get_job(
    job_id: str,
    airflow: AirflowClient = Depends(get_airflow_client),
) -> PredictJobStatusResponse:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_id not found")
    if job["mode"] == "inline":
        return PredictJobStatusResponse(
            job_id=job_id,
            status=job["status"],
            items=[PredictPoint(**i) for i in job.get("items", [])],
        )
    dag_run_id = job.get("dag_run_id")
    if not dag_run_id:
        return PredictJobStatusResponse(job_id=job_id, status=job["status"])
    try:
        run = await airflow.get_dag_run(FORECAST_DAG_ID, dag_run_id)
    except Exception as exc:  # noqa: BLE001
        return PredictJobStatusResponse(
            job_id=job_id, status="error", dag_run_id=dag_run_id, detail=str(exc)
        )
    state = run.get("state") or "unknown"
    mapped = {
        "queued": "queued",
        "running": "running",
        "success": "completed",
        "failed": "failed",
    }.get(state, state)
    job["status"] = mapped
    return PredictJobStatusResponse(
        job_id=job_id, status=mapped, dag_run_id=dag_run_id, dag_state=state
    )


def _predict_inline(
    df: pd.DataFrame, request: Request, dataset: str
) -> tuple[list[PredictPoint], dict[str, Any]]:
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "ItemCode"])

    cache = getattr(request.app.state, "model_cache", None)
    model = cache.get(dataset) if cache is not None else None
    if model is not None and hasattr(model, "predict"):
        try:
            preds = model.predict(df)
            df_out = df.assign(predicted_quantity=preds)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Model.predict failed (%s) — falling back to baseline", exc)
            df_out = _baseline_predict(df)
    else:
        df_out = _baseline_predict(df)

    df_out = (
        df_out.groupby(["ItemCode", "Date"], as_index=False)["predicted_quantity"]
        .mean()
        .sort_values(["ItemCode", "Date"])
    )
    if df_out.empty:
        return [], {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "data": {"values": []}}

    base_date = df_out["Date"].min()
    items: list[PredictPoint] = []
    for row in df_out.itertuples(index=False):
        horizon = (row.Date.date() - base_date.date()).days + 1
        items.append(
            PredictPoint(
                item_code=str(row.ItemCode),
                target_date=row.Date.date(),
                horizon=horizon,
                predicted_quantity=float(row.predicted_quantity),
            )
        )

    chart_spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "description": "Predicted daily demand per SKU",
        "mark": {"type": "line", "point": True},
        "encoding": {
            "x": {"field": "target_date", "type": "temporal", "title": "Date"},
            "y": {"field": "predicted_quantity", "type": "quantitative", "title": "Predicted qty"},
            "color": {"field": "item_code", "type": "nominal", "title": "SKU"},
        },
        "data": {
            "values": [
                {
                    "item_code": p.item_code,
                    "target_date": p.target_date.isoformat(),
                    "predicted_quantity": p.predicted_quantity,
                }
                for p in items[:5000]
            ]
        },
    }
    return items, chart_spec


def _baseline_predict(df: pd.DataFrame) -> pd.DataFrame:
    if "Quantity" in df.columns:
        df["predicted_quantity"] = (
            df.groupby("ItemCode")["Quantity"]
            .transform(lambda s: s.rolling(7, min_periods=1).mean())
            .clip(lower=0)
        )
    else:
        df["predicted_quantity"] = 1.0
    return df
