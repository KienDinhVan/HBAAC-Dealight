from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from time import perf_counter
from typing import Any

from fastapi import FastAPI, HTTPException, Path, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from api.app.agents.drift import DriftAgent
from api.app.agents.forecast import ForecastAgent
from api.app.agents.retrain import RetrainAgent
from api.app.agents.sales import SalesAgent
from api.app.agents.team import TeamLeadAgent
from api.app.clients.airflow import AirflowClient
from api.app.clients.bigquery import OfflineStoreClient
from api.app.clients.duckdb_client import DuckDBClient
from api.app.clients.gcs import GcsUploader
from api.app.clients.openrouter import OpenRouterClient
from api.app.clients.redis_store import OnlineStoreClient
from api.app.config import get_settings
from api.app.infra.approval import ApprovalStore
from api.app.repository import ForecastRepository
from api.app.routers import chat as chat_router_module
from api.app.routers import drift as drift_router_module
from api.app.routers import ingest as ingest_router_module
from api.app.routers import predict as predict_router_module
from api.app.routers import retrain as retrain_router_module
from api.app.schemas import (
    ForecastResponse,
    ForecastRunResponse,
    ForecastSummaryResponse,
    HealthResponse,
    MonitoringReportResponse,
    TopSkusResponse,
    VersionResponse,
)

_logger = logging.getLogger(__name__)

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
FORECAST_ROWS = Gauge(
    "forecast_latest_row_count", "Rows in the latest monitored forecast run."
)
FORECAST_MISSING_SKUS = Gauge(
    "forecast_latest_missing_skus", "Missing SKUs in the latest monitored forecast run."
)
FORECAST_NEGATIVE = Gauge(
    "forecast_latest_negative_predictions",
    "Negative predictions in the latest monitored forecast run.",
)
FORECAST_DRIFT = Gauge(
    "forecast_latest_drift_detected",
    "Whether latest data or prediction drift report raised drift.",
)

settings = get_settings()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialise agents, Airflow client, and DuckDB sales loader on startup."""
    app.state.repository = ForecastRepository(settings.database_url)
    app.state.approval_store = ApprovalStore()
    app.state.airflow_client = AirflowClient(
        base_url=settings.airflow_base_url,
        username=settings.airflow_username,
        password=settings.airflow_password,
    )
    app.state.gcs_uploader = (
        GcsUploader(settings.gcs_bucket, project=settings.gcp_project_id or None)
        if settings.gcs_bucket
        else None
    )
    app.state.offline_store = (
        OfflineStoreClient(settings.gcp_project_id, settings.bq_dataset)
        if settings.gcp_project_id
        else None
    )
    app.state.online_store = (
        OnlineStoreClient(settings.redis_url) if settings.redis_url else None
    )
    app.state.duckdb = None
    app.state.team_lead = None
    app.state.model = None

    if settings.enable_agents:
        try:
            llm = OpenRouterClient(
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
            )
            duckdb = DuckDBClient(
                data_dir=settings.dealight_data_dir,
                database_url=settings.database_url,
            )
            app.state.duckdb = duckdb
            sales_agent = SalesAgent(client=llm, db=duckdb)
            forecast_agent = ForecastAgent(client=llm, db=duckdb)
            drift_agent = DriftAgent(client=llm, repo=app.state.repository)
            retrain_agent = RetrainAgent(client=llm, airflow=app.state.airflow_client)
            app.state.team_lead = TeamLeadAgent(
                client=llm,
                sales_agent=sales_agent,
                forecast_agent=forecast_agent,
                drift_agent=drift_agent,
                retrain_agent=retrain_agent,
            )
            _logger.info("HBAAC TeamLeadAgent ready (Sales/Forecast/Drift/Retrain).")
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Agent init failed — chat/agent endpoints will return 503 but the rest of the API still works."
            )
    else:
        _logger.info("ENABLE_AGENTS=false — skipping agent initialisation.")

    yield


app = FastAPI(
    title="HBAAC-Dealight Workspace API",
    version=settings.service_version,
    description=(
        "Forecast serving + CSV predict, drift monitoring, retrain trigger, "
        "and a multi-agent ReAct chat (TeamLead → Sales/Forecast/Drift/Retrain)."
    ),
    lifespan=_lifespan,
)

# Permissive CORS for the local web workspace + any explicitly-listed origin.
_cors_origins = list(settings.cors_origins) or [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    # Every endpoint is GET or POST; keep cross-origin surface to just those.
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(chat_router_module.router)
app.include_router(predict_router_module.router)
app.include_router(drift_router_module.router)
app.include_router(retrain_router_module.router)
app.include_router(ingest_router_module.router)


def _repository(request: Request) -> ForecastRepository:
    return request.app.state.repository


def _float_values(value: dict[str, Any], *fields: str) -> dict[str, Any]:
    for field in fields:
        if value.get(field) is not None:
            value[field] = float(value[field])
    return value


def _observe_monitoring(report: dict[str, Any] | None) -> None:
    if report is None:
        return
    FORECAST_ROWS.set(report["forecast_row_count"])
    FORECAST_MISSING_SKUS.set(report["missing_sku_count"])
    FORECAST_NEGATIVE.set(report["negative_prediction_count"])
    FORECAST_DRIFT.set(1 if report["drift_detected"] else 0)


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


@app.get("/monitoring/latest", response_model=MonitoringReportResponse)
def latest_monitoring_report(request: Request) -> dict[str, Any]:
    report = _repository(request).latest_monitoring_report()
    if report is None:
        raise HTTPException(status_code=404, detail="No monitoring report found")
    _observe_monitoring(report)
    return _float_values(
        report,
        "prediction_min",
        "prediction_mean",
        "prediction_max",
        "zero_ratio",
    )


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    latest_report = getattr(_repository(request), "latest_monitoring_report", None)
    if latest_report is not None:
        try:
            _observe_monitoring(latest_report())
        except Exception:
            DATABASE_ERROR_COUNT.inc()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
