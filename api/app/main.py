from __future__ import annotations

from datetime import date
from time import perf_counter
from typing import Any

from fastapi import FastAPI, HTTPException, Path, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from api.app.config import get_settings
from api.app.repository import ForecastRepository
from api.app.schemas import (
    ForecastResponse,
    ForecastRunResponse,
    ForecastSummaryResponse,
    HealthResponse,
    TopSkusResponse,
    VersionResponse,
)

ITEM_CODE_PATTERN = r"^[A-Za-z0-9._\-]{1,64}$"

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total API requests.",
    ["method", "route", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "API request latency in seconds.",
    ["method", "route"],
)
NOT_FOUND_COUNT = Counter(
    "forecast_not_found_total", "Forecast queries returning no result."
)
DATABASE_ERROR_COUNT = Counter(
    "database_connection_errors_total", "Database connection failures."
)

settings = get_settings()
app = FastAPI(
    title="SKU Forecast API",
    version=settings.service_version,
    description="Read-only API serving precomputed 56-day batch forecasts.",
)
app.state.repository = ForecastRepository(settings.database_url)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )


def _repository(request: Request) -> ForecastRepository:
    return request.app.state.repository


def _float_values(value: dict[str, Any], *fields: str) -> dict[str, Any]:
    for field in fields:
        if value.get(field) is not None:
            value[field] = float(value[field])
    return value


@app.middleware("http")
async def observe_requests(request: Request, call_next: Any) -> Response:
    start = perf_counter()
    response: Response | None = None
    try:
        response = await call_next(request)
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        code = response.status_code if response is not None else 500
        REQUEST_COUNT.labels(request.method, path, str(code)).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(perf_counter() - start)


@app.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    repository = _repository(request)
    try:
        database_ready = repository.ping()
        forecast_ready = (
            repository.latest_run() is not None if database_ready else False
        )
    except Exception as exc:
        DATABASE_ERROR_COUNT.inc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc
    if not database_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        )
    return HealthResponse(
        status="ok", service=settings.service_name, forecast_ready=forecast_ready
    )


@app.get("/version", response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(
        service=settings.service_name, version=settings.service_version
    )


@app.get("/forecast-runs/latest", response_model=ForecastRunResponse)
def latest_forecast_run(request: Request) -> dict[str, Any]:
    run = _repository(request).latest_run()
    if run is None:
        NOT_FOUND_COUNT.inc()
        raise HTTPException(status_code=404, detail="No successful forecast run found")
    return run


@app.get("/model/current", response_model=ForecastRunResponse)
def current_model(request: Request) -> dict[str, Any]:
    return latest_forecast_run(request)


@app.get("/forecast/top-skus", response_model=TopSkusResponse)
def get_top_skus(
    target_date: date,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    run, points = _repository(request).top_skus(target_date, limit, offset)
    if run is None or not points:
        NOT_FOUND_COUNT.inc()
        raise HTTPException(
            status_code=404, detail="No forecasts found for target date"
        )
    return {
        "forecast_date": run["forecast_date"],
        "target_date": target_date,
        "model_name": run["model_name"],
        "model_version": run["model_version"],
        "limit": limit,
        "offset": offset,
        "items": [_float_values(point, "predicted_quantity") for point in points],
    }


@app.get("/forecast/summary", response_model=ForecastSummaryResponse)
def get_summary(target_date: date, request: Request) -> dict[str, Any]:
    run, summary = _repository(request).summary(target_date)
    if run is None or summary is None:
        NOT_FOUND_COUNT.inc()
        raise HTTPException(
            status_code=404, detail="No forecasts found for target date"
        )
    summary = _float_values(
        summary,
        "total_predicted_quantity",
        "avg_predicted_quantity",
        "max_predicted_quantity",
    )
    return {
        **summary,
        "forecast_date": run["forecast_date"],
        "model_name": run["model_name"],
        "model_version": run["model_version"],
    }


@app.get("/forecast/{item_code}", response_model=ForecastResponse)
def get_forecast(
    request: Request,
    item_code: str = Path(pattern=ITEM_CODE_PATTERN),
    days: int = Query(default=56, ge=1, le=56),
    forecast_date: date | None = Query(default=None),
) -> dict[str, Any]:
    run, points = _repository(request).forecast(item_code, days, forecast_date)
    if run is None or not points:
        NOT_FOUND_COUNT.inc()
        raise HTTPException(
            status_code=404, detail=f"Forecast not found for {item_code}"
        )
    return {
        "item_code": item_code,
        "forecast_date": run["forecast_date"],
        "model_name": run["model_name"],
        "model_version": run["model_version"],
        "forecast": [_float_values(point, "predicted_quantity") for point in points],
    }


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
