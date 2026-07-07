from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    service_name: str = "sku-forecast-api"
    service_version: str = "0.1.0"
    database_url: str = "postgresql://forecast:forecast@localhost:5432/sku_forecasting"
    production_submission_path: str = (
        "data/artifacts/"
        "submission_FINAL_twostage_top300_lb730_s7_b1200_seedens20_alpha0.575_"
        "keysku_cautious_mapoldnew_a0.05.csv"
    )
    forecast_date: str = "2025-09-05"
    forecast_run_id: str = "submission-final-public-048729-20250905"
    model_name: str = "twostage-seed-ensemble-mapoldnew"
    model_version: str = "public-0.48729"
    cors_origins: tuple[str, ...] = ()

    # --- Workspace web extension (Sprint 9) ---
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash-preview-05-20"
    airflow_base_url: str = "http://airflow-webserver:8080"
    airflow_username: str = "airflow"
    airflow_password: str = "airflow"
    dealight_data_dir: str = "/opt/project/data/raw"
    upload_dir: str = "/opt/project/data/uploads"
    monitoring_dir: str = "/opt/project/data/monitoring"
    mlflow_tracking_uri: str = "http://mlflow:5000"
    mlflow_model_uri: str = ""
    inline_predict_max_rows: int = 50_000
    enable_agents: bool = True
    # --- DE GCS pipeline (DE_arch) ---
    gcp_project_id: str = ""
    gcs_bucket: str = ""
    bq_dataset: str = "dealight"
    max_upload_bytes: int = 104_857_600  # 100 MiB cap for /ingest/upload


def _parse_origins(raw: str) -> tuple[str, ...]:
    return tuple(origin.strip() for origin in raw.split(",") if origin.strip())


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    defaults = Settings()
    return Settings(
        service_name=os.getenv("SERVICE_NAME", defaults.service_name),
        service_version=os.getenv("SERVICE_VERSION", defaults.service_version),
        database_url=os.getenv("DATABASE_URL", defaults.database_url),
        production_submission_path=os.getenv(
            "PRODUCTION_SUBMISSION_PATH", defaults.production_submission_path
        ),
        forecast_date=os.getenv("FORECAST_DATE", defaults.forecast_date),
        forecast_run_id=os.getenv("FORECAST_RUN_ID", defaults.forecast_run_id),
        model_name=os.getenv("MODEL_NAME", defaults.model_name),
        model_version=os.getenv("MODEL_VERSION", defaults.model_version),
        cors_origins=_parse_origins(os.getenv("CORS_ORIGINS", "")),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", defaults.openrouter_api_key),
        openrouter_model=os.getenv("OPENROUTER_MODEL", defaults.openrouter_model),
        airflow_base_url=os.getenv("AIRFLOW_BASE_URL", defaults.airflow_base_url),
        airflow_username=os.getenv("AIRFLOW_USERNAME", defaults.airflow_username),
        airflow_password=os.getenv("AIRFLOW_PASSWORD", defaults.airflow_password),
        dealight_data_dir=os.getenv("DEALIGHT_DATA_DIR", defaults.dealight_data_dir),
        upload_dir=os.getenv("UPLOAD_DIR", defaults.upload_dir),
        monitoring_dir=os.getenv("MONITORING_DIR", defaults.monitoring_dir),
        mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI", defaults.mlflow_tracking_uri),
        mlflow_model_uri=os.getenv("MLFLOW_MODEL_URI", defaults.mlflow_model_uri),
        inline_predict_max_rows=int(
            os.getenv("INLINE_PREDICT_MAX_ROWS", defaults.inline_predict_max_rows)
        ),
        enable_agents=_env_bool("ENABLE_AGENTS", defaults.enable_agents),
        gcp_project_id=os.getenv("GCP_PROJECT_ID", defaults.gcp_project_id),
        gcs_bucket=os.getenv("GCS_BUCKET", defaults.gcs_bucket),
        bq_dataset=os.getenv("BQ_DATASET", defaults.bq_dataset),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", defaults.max_upload_bytes)),
    )
