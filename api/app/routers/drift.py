from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from api.app.config import get_settings
from api.app.deps import get_repository
from api.app.repository import ForecastRepository
from api.app.schemas import DriftReportListItem, DriftReportListResponse

router = APIRouter(prefix="/drift", tags=["drift"])
_logger = logging.getLogger(__name__)


@router.get("/reports", response_model=DriftReportListResponse)
def list_drift_reports(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repo: ForecastRepository = Depends(get_repository),
) -> DriftReportListResponse:
    reports = repo.list_monitoring_reports(limit=limit, offset=offset)
    return DriftReportListResponse(
        items=[DriftReportListItem(**_subset(r)) for r in reports],
        limit=limit,
        offset=offset,
    )


@router.get("/reports/{report_id}/html")
def get_drift_html(
    report_id: str,
    type: Literal["data", "prediction"] = Query("data"),
    repo: ForecastRepository = Depends(get_repository),
) -> FileResponse:
    report = repo.get_monitoring_report(report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report_id not found")
    field = "data_drift_report_path" if type == "data" else "prediction_drift_report_path"
    rel_path = report.get(field)
    if not rel_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {type} drift HTML for this report")

    settings = get_settings()
    candidate = _monitoring_candidate(Path(rel_path), Path(settings.monitoring_dir))

    monitoring_root = Path(settings.monitoring_dir).resolve()
    try:
        resolved = candidate.resolve()
        resolved.relative_to(monitoring_root)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report file not found or out of bounds")

    if not resolved.exists() or resolved.suffix.lower() != ".html":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report HTML not on disk")
    return FileResponse(
        resolved,
        media_type="text/html",
        headers={
            "Content-Security-Policy": "frame-ancestors 'self'",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _monitoring_candidate(path: Path, monitoring_dir: Path) -> Path:
    if path.is_absolute():
        return path

    parts = path.parts
    if len(parts) >= 2 and parts[0] == "data" and parts[1] == "monitoring":
        path = Path(*parts[2:]) if len(parts) > 2 else Path()
    elif len(parts) >= 1 and parts[0] == "monitoring":
        path = Path(*parts[1:]) if len(parts) > 1 else Path()

    candidate = path
    if not candidate.is_absolute():
        candidate = monitoring_dir / candidate
    return candidate


def _subset(report: dict) -> dict:
    keys = {
        "report_id", "run_id", "generated_at", "status",
        "drift_detected", "forecast_row_count",
        "missing_sku_count", "negative_prediction_count", "alerts",
    }
    return {k: report.get(k) for k in keys}
