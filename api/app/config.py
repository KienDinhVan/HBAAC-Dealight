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
    )
