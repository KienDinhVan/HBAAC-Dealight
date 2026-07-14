"""Dataset registry and dataset-scoped forecast endpoints."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from api.app.schemas import ForecastSummaryResponse
from hbacc_prj.dataset_config import DatasetConfig, load_all_dataset_configs

router = APIRouter(prefix="/api/v1", tags=["datasets"])


def _configs() -> dict[str, DatasetConfig]:
    directory = Path(os.environ.get("DATASETS_DIR", "datasets"))
    return {config.name: config for config in load_all_dataset_configs(directory)}


def _model_names(config: DatasetConfig) -> tuple[str, ...]:
    dataset_model = f"{config.name}-forecaster"
    if config.name == "hbaac_sku":
        return ("sku-demand-lightgbm", dataset_model)
    return (dataset_model,)


def _float_values(value: dict[str, Any], *fields: str) -> dict[str, Any]:
    for field in fields:
        if value.get(field) is not None:
            value[field] = float(value[field])
    return value


def _dataset_item(config: DatasetConfig) -> dict[str, Any]:
    model_names = _model_names(config)
    stages = ("ingest", "features", "train", "forecast", "monitor")
    return {
        "name": config.name,
        "source_type": config.source.type,
        "source_format": config.source.format if config.source.type == "file" else None,
        "schedule": config.schedule,
        "training_schedule": config.training.schedule,
        "validation_days": config.training.validation_days,
        "model_name": model_names[0],
        "table_name": config.table_name,
        "mapping": {
            "entity_id": config.mapping.entity_id,
            "ds": config.mapping.ds,
            "quantity": config.mapping.quantity,
            "attrs": config.mapping.attrs,
        },
        "dags": [
            {
                "stage": stage,
                "dag_id": f"{stage}_{config.name}",
                "schedule": (
                    config.schedule
                    if stage == "ingest"
                    else config.training.schedule if stage == "train" else None
                ),
            }
            for stage in stages
        ],
    }


@router.get("/datasets")
def list_datasets() -> list[dict[str, Any]]:
    return [_dataset_item(config) for config in _configs().values()]


@router.get(
    "/{dataset}/forecast/summary", response_model=ForecastSummaryResponse
)
def dataset_forecast_summary(
    dataset: str, target_date: date, request: Request
) -> dict[str, Any]:
    configs = _configs()
    config = configs.get(dataset)
    if config is None:
        raise HTTPException(status_code=404, detail=f"unknown dataset '{dataset}'")

    run, summary = request.app.state.repository.summary(
        target_date, model_names=_model_names(config)
    )
    if run is None or summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"No forecasts found for dataset '{dataset}' and target date",
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
