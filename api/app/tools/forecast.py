import json
import logging

from api.app.clients.duckdb_client import DuckDBClient
from .base import tool, Tool

_logger = logging.getLogger(__name__)

_SCHEMA_DOC = """
Dealight Forecast Database — DuckDB

=== TABLE: forecasts ===
PURPOSE: 56-day demand forecasts per SKU produced by the two-stage LightGBM model.

COLUMNS:
  sku            (string)  — SKU identifier, e.g. 'SKU-00001'
  forecast_date  (date)    — The calendar date being forecast
  day_offset     (int)     — Days after the training cutoff (1 = first forecast day, 56 = last)
  forecast_qty   (float)   — Predicted sales quantity for that day

NOTES:
  - Current serving run is anchored on 2025-09-05 and covers target dates
    2025-09-06 through 2025-10-31.
  - day_offset 1-28 = validation window; 29-56 = evaluation window
  - forecast_qty = 0 means the model predicts no sales (SKU inactive or low-demand period)
  - Only ~300 high-profit SKUs have non-trivial forecasts; the rest are baseline (median)

=== TABLE: sku_profile ===
PURPOSE: Aggregated per-SKU stats (also usable here for joining with forecasts).

COLUMNS:
  ItemCode, total_profit, profit_weight, active_days, zero_ratio, avg_daily_qty, return_ratio

=== TABLE: sales ===
PURPOSE: Historical daily transactions (available for context joins).

COMMON QUERY PATTERNS:
  -- Total forecasted demand per SKU for the next 28 days
  SELECT sku, SUM(forecast_qty) AS total_forecast
  FROM forecasts WHERE day_offset <= 28
  GROUP BY sku ORDER BY total_forecast DESC LIMIT 20

  -- SKUs with highest forecast demand (full 56-day window)
  SELECT sku, SUM(forecast_qty) AS total_56day_forecast
  FROM forecasts
  GROUP BY sku ORDER BY total_56day_forecast DESC LIMIT 20

  -- Daily aggregate forecast trend
  SELECT forecast_date, SUM(forecast_qty) AS total_demand
  FROM forecasts GROUP BY forecast_date ORDER BY forecast_date

  -- Stockout risk: active SKUs with near-zero forecast
  SELECT f.sku, SUM(f.forecast_qty) AS forecast_28d,
         p.avg_daily_qty AS historical_avg
  FROM forecasts f
  JOIN sku_profile p ON f.sku = p.ItemCode
  WHERE f.day_offset <= 28 AND p.active_days >= 30
  GROUP BY f.sku, p.avg_daily_qty
  ORDER BY forecast_28d ASC LIMIT 30
"""


def make_forecast_tools(db: DuckDBClient) -> list[Tool]:

    @tool
    def get_forecast_schema() -> str:
        """
        Retrieve the schema and documentation for the Dealight forecast tables.

        Returns:
            str: Full schema documentation including column descriptions and example queries.
        """
        _logger.info("Fetching forecast schema")
        return _SCHEMA_DOC

    @tool
    def query_forecast(sql_query: str) -> str:
        """
        Execute a SQL query against the Dealight forecast database and return results.

        Args:
            sql_query (str): SQL query to run against forecasts, sku_profile, or sales tables.
        Returns:
            str: Query results formatted as a text table, or an error message.
        """
        _logger.info("Executing forecast query")
        return db.execute_query(sql_query)

    @tool
    def query_forecast_and_visualize(query: str, spec: str) -> str:
        """
        Execute a SQL query against the forecast tables and return results embedded
        in a Vega-Lite spec ready for chart rendering on the frontend.

        On success returns JSON with type "chart". On failure returns type "query_error".

        Args:
            query (str): SQL query against forecasts, sku_profile, or sales tables.
            spec (str): Vega-Lite specification as a JSON string (without a data field).
        Returns:
            str: JSON string — either {"type": "chart", "spec": {...}} or {"type": "query_error", ...}.
        """
        try:
            parsed_spec = json.loads(spec)
        except json.JSONDecodeError as e:
            return json.dumps({
                "type": "query_error",
                "status": "INVALID_SPEC",
                "reason": f"Vega-Lite spec is not valid JSON: {e}",
                "error_type": "INVALID_SPEC",
                "suggestion": "Ensure the spec argument is a valid JSON string.",
            })

        result = db.execute_query_as_records(query)

        if not result["ok"]:
            return json.dumps({
                "type": "query_error",
                "status": result["status"],
                "reason": result["reason"],
                "error_type": result["error_type"],
                "suggestion": _forecast_error_suggestion(result["error_type"]),
            })

        if not result["records"]:
            return json.dumps({
                "type": "query_error",
                "status": "NO_DATA",
                "reason": "Query executed successfully but returned no rows.",
                "error_type": "NO_DATA",
                "suggestion": "Try broadening the date range or adjusting filters.",
            })

        parsed_spec["data"] = {"values": result["records"]}
        return json.dumps({"type": "chart", "spec": parsed_spec})

    @tool
    def detect_stockout_risk(top_n: int = 20) -> str:
        """
        Identify SKUs at risk of stockout: active SKUs whose forecasted demand over the
        next 28 days is significantly lower than their historical average — suggesting
        the model detected a demand drop that may leave inventory stranded, or conversely
        that demand is high but supply may not keep up.

        Returns a ranked table of at-risk SKUs with their forecast vs historical comparison.

        Args:
            top_n (int): Number of highest-risk SKUs to return (default 20).
        Returns:
            str: Ranked table of SKUs at stockout risk.
        """
        sql = f"""
        SELECT
            f.sku,
            ROUND(SUM(f.forecast_qty), 1)          AS forecast_28d_qty,
            ROUND(p.avg_daily_qty * 28, 1)          AS expected_28d_qty,
            ROUND(p.avg_daily_qty, 2)               AS avg_daily_qty,
            p.active_days,
            ROUND(p.total_profit, 0)                AS total_profit,
            CASE
                WHEN p.avg_daily_qty * 28 = 0 THEN 0
                ELSE ROUND(SUM(f.forecast_qty) / (p.avg_daily_qty * 28), 2)
            END AS demand_ratio
        FROM forecasts f
        JOIN sku_profile p ON f.sku = p.ItemCode
        WHERE f.day_offset <= 28
          AND p.active_days >= 30
          AND p.avg_daily_qty > 0
        GROUP BY f.sku, p.avg_daily_qty, p.active_days, p.total_profit
        HAVING SUM(f.forecast_qty) < p.avg_daily_qty * 28 * 0.5
        ORDER BY demand_ratio ASC
        LIMIT {top_n}
        """
        _logger.info("Detecting stockout risk (top %d)", top_n)
        return db.execute_query(sql)

    @tool
    def detect_overstock_risk(top_n: int = 20) -> str:
        """
        Identify SKUs at risk of overstock: SKUs whose forecasted demand over
        the next 28 days is significantly higher than their historical average —
        requiring extra inventory procurement to meet demand.

        Args:
            top_n (int): Number of highest-risk SKUs to return (default 20).
        Returns:
            str: Ranked table of SKUs at overstock / high-demand risk.
        """
        sql = f"""
        SELECT
            f.sku,
            ROUND(SUM(f.forecast_qty), 1)      AS forecast_28d_qty,
            ROUND(p.avg_daily_qty * 28, 1)     AS expected_28d_qty,
            ROUND(p.avg_daily_qty, 2)          AS avg_daily_qty,
            p.active_days,
            ROUND(p.total_profit, 0)           AS total_profit,
            CASE
                WHEN p.avg_daily_qty * 28 = 0 THEN 999
                ELSE ROUND(SUM(f.forecast_qty) / (p.avg_daily_qty * 28), 2)
            END AS demand_ratio
        FROM forecasts f
        JOIN sku_profile p ON f.sku = p.ItemCode
        WHERE f.day_offset <= 28
          AND p.active_days >= 30
          AND p.avg_daily_qty > 0
        GROUP BY f.sku, p.avg_daily_qty, p.active_days, p.total_profit
        HAVING SUM(f.forecast_qty) > p.avg_daily_qty * 28 * 1.5
        ORDER BY demand_ratio DESC
        LIMIT {top_n}
        """
        _logger.info("Detecting overstock risk (top %d)", top_n)
        return db.execute_query(sql)

    return [
        get_forecast_schema,
        query_forecast,
        query_forecast_and_visualize,
        detect_stockout_risk,
        detect_overstock_risk,
    ]


def _forecast_error_suggestion(error_type: str) -> str:
    suggestions = {
        "NO_DATA": "Broaden filters or check that the forecasts table is populated.",
        "SYNTAX_ERROR": "Review SQL syntax — check for missing commas or unsupported clauses.",
        "TABLE_NOT_FOUND": "Use table names: forecasts, sku_profile, sales. Call get_forecast_schema().",
        "COLUMN_NOT_FOUND": "Check column names. Call get_forecast_schema() to list available columns.",
        "QUERY_FAILED": "An unexpected error occurred. Check the reason field for details.",
    }
    return suggestions.get(error_type, "Check the query and try again.")
