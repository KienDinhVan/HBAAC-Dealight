from datetime import date

from api.app.clients.airflow import AirflowClient
from api.app.clients.openrouter import OpenRouterClient as AIHubClient
from api.app.tools.retrain import make_retrain_tools

from .base import ReactAgent


def _build_system_prompt() -> str:
    today = date.today().isoformat()
    return f"""\
You are the HBAAC Retrain Operator.
Today is {today}.

You can:
  - trigger_retrain(reason, feature_version?) — runs Airflow DAG `dag_03_train_model`.
    This tool requires explicit user approval before execution.
  - get_retrain_status(dag_run_id) — current state of a triggered run.
  - list_recent_retrain_runs(limit) — last N retrain DAG runs.

Rules:
  1. ALWAYS confirm the reason with the user before calling trigger_retrain.
     The reason field is what oncall sees in the Airflow note.
  2. Never trigger a second retrain while a previous one is still queued/running —
     check list_recent_retrain_runs first.
  3. After a retrain is triggered, return the dag_run_id so the user can poll status.
  4. Reasonable reasons: "data drift detected", "model accuracy regression",
     "scheduled refresh", "newly ingested data".

Respond directly with a clear plain-text summary. Do NOT call any finish tool.
"""


class RetrainAgent(ReactAgent):
    def __init__(self, client: AIHubClient, airflow: AirflowClient) -> None:
        super().__init__(
            client=client,
            system_prompt=_build_system_prompt(),
            tools=make_retrain_tools(airflow),
            max_rounds=5,
            agent_name="RetrainAgent",
        )
