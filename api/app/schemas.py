from __future__ import annotations

from datetime import date, datetime

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
