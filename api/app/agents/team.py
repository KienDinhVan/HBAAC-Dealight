from api.app.clients.openrouter import OpenRouterClient as AIHubClient
from api.app.tools.base import AgentTool

from .base import ReactAgent
from .drift import DriftAgent
from .forecast import ForecastAgent
from .retrain import RetrainAgent
from .sales import SalesAgent

_SYSTEM_PROMPT = """\
You are the HBAAC-Dealight Analytics Team Lead. You coordinate specialist agents
to answer retail business questions about sales, demand forecasting, model drift,
and MLOps retraining.

Available specialists:
- SalesAgent    : Historical sales analysis — revenue, profit, top SKUs, return
                  rates, demand trends. Queries the sales & sku_profile tables.
- ForecastAgent : Demand forecasting and inventory risk — 56-day forecasts per
                  SKU, stockout/overstock detection, forecast vs history.
- DriftAgent    : Monitoring & drift reports — data quality, drift_detected
                  status, accuracy regression. Reads serving.monitoring_reports.
- RetrainAgent  : Trigger and observe Airflow training runs. Requires explicit
                  user approval before launching a retrain.

Instructions:
1. Identify which specialist(s) best fit the user's question.
2. Delegate to each relevant specialist using the delegation tools.
3. When the user reports a problem (e.g. "drift is high", "predictions look off"),
   first delegate to DriftAgent for diagnosis. Only delegate to RetrainAgent if
   the user explicitly asks to retrain or once DriftAgent has confirmed drift is
   actionable.
4. For multi-domain questions, delegate to multiple specialists before synthesising.
5. Always include actionable business or MLOps recommendations in your final answer.
6. If a specialist returns no data or an error, surface it clearly and do NOT
   re-delegate to the same specialist for the same failed request.

Do NOT call any finish tool. Respond directly with your synthesized answer.
"""


class TeamLeadAgent(ReactAgent):
    def __init__(
        self,
        client: AIHubClient,
        sales_agent: SalesAgent,
        forecast_agent: ForecastAgent,
        drift_agent: DriftAgent,
        retrain_agent: RetrainAgent,
    ) -> None:
        tools = [
            AgentTool(
                name="delegate_to_sales_agent",
                description=(
                    "Delegate to the Sales specialist for historical sales questions. "
                    "Use for: revenue trends, top/bottom performing SKUs, profit analysis, "
                    "return rates, monthly/weekly breakdowns, and any query on past transactions."
                ),
                agent=sales_agent,
            ),
            AgentTool(
                name="delegate_to_forecast_agent",
                description=(
                    "Delegate to the Forecast specialist for demand forecasting and inventory risk. "
                    "Use for: 56-day demand forecasts, stockout risk alerts, overstock risk alerts, "
                    "forecast vs historical demand comparison, and inventory planning."
                ),
                agent=forecast_agent,
            ),
            AgentTool(
                name="delegate_to_drift_agent",
                description=(
                    "Delegate to the Drift / Monitoring specialist. "
                    "Use for: 'is drift detected?', model quality regressions, missing-SKU "
                    "or negative-prediction anomalies, daily monitoring report summaries."
                ),
                agent=drift_agent,
            ),
            AgentTool(
                name="delegate_to_retrain_agent",
                description=(
                    "Delegate to the Retrain Operator. "
                    "Use ONLY when the user explicitly asks to retrain the model or to check "
                    "the status of a running/recent retrain. Requires user approval before triggering."
                ),
                agent=retrain_agent,
            ),
        ]
        super().__init__(
            client=client,
            system_prompt=_SYSTEM_PROMPT,
            tools=tools,
            agent_name="TeamLeadAgent",
        )
