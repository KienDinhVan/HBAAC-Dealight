from __future__ import annotations

import json
import logging
from typing import Any

from api.app.repository import ForecastRepository
from .base import Tool, tool

_logger = logging.getLogger(__name__)

_SCHEMA_DOC = """
HBAAC Monitoring & Drift store (Postgres monitoring.forecast_reports)

Each row is one daily Evidently-backed drift report.

KEY FIELDS:
  report_id              (string)  — unique id of the report (e.g. sprint-07-dag-20260527)
  run_id                 (string)  — associated forecast run id
  generated_at           (timestamp)
  status                 (string)  — ok | warn | critical
  forecast_row_count     (int)
  sku_count              (int)
  horizon_count          (int)
  missing_sku_count      (int)     — SKUs expected but missing in the run
  negative_prediction_count (int)  — predictions clipped below 0 (data quality issue)
  prediction_min/mean/max(float)
  zero_ratio             (float)   — fraction of zero predictions
  actual_row_count       (int)
  accuracy_metrics       (jsonb)   — e.g. {"wrmsse": 0.48729, "mape": 0.21}
  drift_detected         (bool)
  drift_metrics          (jsonb)   — Evidently per-feature drift scores
  alerts                 (string[])
  data_drift_report_path (string?) — HTML file under data/monitoring/
  prediction_drift_report_path (string?) — HTML file under data/monitoring/
"""


def make_drift_tools(repo: ForecastRepository) -> list[Tool]:

    @tool
    def get_drift_schema() -> str:
        """Return the schema/columns documentation for the drift report store."""
        return _SCHEMA_DOC

    @tool
    def get_latest_drift_report() -> str:
        """Return the most recent monitoring/drift report as a JSON string."""
        report = repo.latest_monitoring_report()
        if report is None:
            return json.dumps({"status": "no_report", "message": "No monitoring report found."})
        return json.dumps(_normalize_report(report), default=str)

    @tool
    def list_drift_reports(limit: int = 10) -> str:
        """List the most recent drift reports (newest first).

        Args:
            limit: Maximum number of reports to return (default 10, max 100).
        Returns:
            JSON-encoded list of report summaries.
        """
        limit_int = max(1, min(int(limit), 100))
        reports = repo.list_monitoring_reports(limit=limit_int)
        return json.dumps(
            [_normalize_report(r, summary=True) for r in reports], default=str
        )

    @tool
    def get_drift_report(report_id: str) -> str:
        """Return a single monitoring report by id.

        Args:
            report_id: The report identifier (e.g. sprint-07-dag-20260527).
        Returns:
            JSON-encoded report or an error message.
        """
        report = repo.get_monitoring_report(report_id)
        if report is None:
            return json.dumps({"status": "not_found", "report_id": report_id})
        return json.dumps(_normalize_report(report), default=str)

    return [get_drift_schema, get_latest_drift_report, list_drift_reports, get_drift_report]


def _normalize_report(report: dict[str, Any], summary: bool = False) -> dict[str, Any]:
    keys_summary = {
        "report_id", "run_id", "generated_at", "status", "drift_detected",
        "forecast_row_count", "missing_sku_count", "negative_prediction_count",
        "alerts",
    }
    out: dict[str, Any] = {}
    for k, v in report.items():
        if summary and k not in keys_summary:
            continue
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[k] = v
    return out
