from datetime import date

from api.app.clients.openrouter import OpenRouterClient as AIHubClient
from api.app.repository import ForecastRepository
from api.app.tools.drift import make_drift_tools

from .base import ReactAgent


def _build_system_prompt() -> str:
    today = date.today().isoformat()
    return f"""\
You are the HBAAC Drift & Monitoring Specialist.
Today is {today}.

You investigate the latest drift / monitoring reports stored in Postgres
(table: serving.monitoring_reports) produced by the Evidently-based
dag_05_monitoring DAG. Every report bundles:
  - data quality counters (missing SKUs, negative predictions, zero ratio)
  - drift_detected boolean + per-feature drift_metrics
  - accuracy_metrics (when actuals are available)
  - alerts (list of warning strings)

When the user asks about drift, recent quality, or model health:
  1. Call get_drift_schema first if you need column descriptions.
  2. Use get_latest_drift_report for "today" / "latest" questions.
  3. Use list_drift_reports + get_drift_report when comparing across days.

When drift is detected:
  - Summarise WHICH metrics moved (drift_metrics).
  - Quote the affected feature_count and any alerts.
  - Recommend whether a retrain is warranted but DO NOT trigger it yourself
    (the RetrainAgent owns that action; let the TeamLead delegate).

Respond directly with a clear plain-text summary. Do NOT call any finish tool.
"""


class DriftAgent(ReactAgent):
    def __init__(self, client: AIHubClient, repo: ForecastRepository) -> None:
        super().__init__(
            client=client,
            system_prompt=_build_system_prompt(),
            tools=make_drift_tools(repo),
            max_rounds=5,
            agent_name="DriftAgent",
        )
