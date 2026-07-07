from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    forecast_ready: bool


class VersionResponse(BaseModel):
    service: str
    version: str


class ForecastPoint(BaseModel):
    target_date: date
    horizon: int
    predicted_quantity: float


class ForecastResponse(BaseModel):
    item_code: str
    forecast_date: date
    model_name: str
    model_version: str
    forecast: list[ForecastPoint]


class ForecastRunResponse(BaseModel):
    run_id: str
    forecast_date: date
    model_name: str
    model_version: str
    status: str
    row_count: int | None
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None


class TopSkuPoint(BaseModel):
    item_code: str
    target_date: date
    horizon: int
    predicted_quantity: float


class TopSkusResponse(BaseModel):
    forecast_date: date
    target_date: date
    model_name: str
    model_version: str
    limit: int
    offset: int
    items: list[TopSkuPoint]


class ForecastSummaryResponse(BaseModel):
    forecast_date: date
    target_date: date
    model_name: str
    model_version: str
    sku_count: int
    total_predicted_quantity: float
    avg_predicted_quantity: float
    max_predicted_quantity: float


class MonitoringReportResponse(BaseModel):
    report_id: str
    run_id: str
    generated_at: datetime
    status: str
    forecast_row_count: int
    sku_count: int
    horizon_count: int
    missing_sku_count: int
    negative_prediction_count: int
    prediction_min: float | None
    prediction_mean: float | None
    prediction_max: float | None
    zero_ratio: float | None
    actual_row_count: int
    accuracy_metrics: dict[str, Any]
    drift_detected: bool
    drift_metrics: dict[str, Any]
    alerts: list[str]
    data_drift_report_path: str | None
    prediction_drift_report_path: str | None


# ---------------------------------------------------------------------------
# Workspace web extension (Sprint 9)
# ---------------------------------------------------------------------------


class PredictPoint(BaseModel):
    item_code: str
    target_date: date
    horizon: int
    predicted_quantity: float


class PredictJobResponse(BaseModel):
    job_id: str
    mode: str
    status: str
    rows: int | None = None
    items: list[PredictPoint] | None = None
    chart_spec: dict[str, Any] | None = None
    dag_run_id: str | None = None
    detail: str | None = None


class PredictJobStatusResponse(BaseModel):
    job_id: str
    status: str
    dag_run_id: str | None = None
    dag_state: str | None = None
    items: list[PredictPoint] | None = None
    detail: str | None = None


class DriftReportListItem(BaseModel):
    report_id: str
    run_id: str
    generated_at: datetime
    status: str
    drift_detected: bool
    forecast_row_count: int
    missing_sku_count: int
    negative_prediction_count: int
    alerts: list[str]


class DriftReportListResponse(BaseModel):
    items: list[DriftReportListItem]
    limit: int
    offset: int


class RetrainTriggerRequest(BaseModel):
    reason: str
    feature_version: str | None = None


class RetrainTriggerResponse(BaseModel):
    dag_id: str
    dag_run_id: str
    state: str | None = None
    note: str | None = None


class RetrainRunStatusResponse(BaseModel):
    dag_id: str
    dag_run_id: str
    state: str | None = None
    execution_date: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    note: str | None = None


class IngestUploadResponse(BaseModel):
    batch_id: str
    source_uri: str
    dag_id: str
    dag_run_id: str
    state: str | None = None


class IngestRunStatusResponse(BaseModel):
    dag_id: str
    dag_run_id: str
    state: str | None = None
    execution_date: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    note: str | None = None
