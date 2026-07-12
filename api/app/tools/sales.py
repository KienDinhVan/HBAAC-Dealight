import json
import logging
from typing import Optional

from api.app.clients.duckdb_client import DuckDBClient
from .base import tool, Tool

_logger = logging.getLogger(__name__)

_SCHEMA_DOC = """
Dealight Retail Sales Database — DuckDB

=== TABLE: sales ===
PURPOSE: Daily retail transactions. Use for historical sales analysis, revenue trends, SKU performance.

COLUMNS:
  Date           (date)    — Transaction date
  ItemCode       (string)  — SKU identifier, e.g. 'SKU-00001'
  Quantity       (int)     — Total units (negative = returns)
  sales_qty      (float)   — Units sold (returns excluded, min 0)
  return_qty     (float)   — Units returned (positive number)
  net_qty        (float)   — Net units = sales - returns
  sales_amount   (float)   — Revenue in VND
  cost_amount    (float)   — COGS in VND
  profit         (float)   — Gross profit = sales_amount - cost_amount
  unit_price     (float)   — Selling price per unit
  unit_cost      (float)   — Cost price per unit

COMMON QUERY PATTERNS:
  -- Top SKUs by revenue in a period
  SELECT ItemCode, SUM(sales_amount) AS revenue
  FROM sales WHERE Date >= '2025-01-01'
  GROUP BY ItemCode ORDER BY revenue DESC LIMIT 10

  -- Daily revenue trend
  SELECT Date, SUM(sales_amount) AS revenue, SUM(profit) AS profit
  FROM sales GROUP BY Date ORDER BY Date

  -- Monthly sales by SKU
  SELECT strftime(Date, '%Y-%m') AS month, ItemCode, SUM(sales_qty) AS units
  FROM sales GROUP BY month, ItemCode ORDER BY month, units DESC

  -- Return rate by SKU
  SELECT ItemCode,
         SUM(return_qty) / NULLIF(SUM(sales_qty), 0) AS return_rate
  FROM sales GROUP BY ItemCode ORDER BY return_rate DESC LIMIT 20

=== TABLE: sku_profile ===
PURPOSE: Aggregated per-SKU stats over the full training period.

COLUMNS:
  ItemCode          (string) — SKU identifier
  total_sales_qty   (float)  — Total units sold ever
  total_sales_amount(float)  — Total revenue ever
  total_profit      (float)  — Total gross profit ever
  active_days       (int)    — Days with at least 1 sale
  avg_daily_qty     (float)  — Average daily sales quantity
  return_qty        (float)  — Total units returned
  profit_weight     (float)  — Share of total positive profit (0-1, sums to ~1)
  zero_ratio        (float)  — Fraction of days with zero sales
  return_ratio      (float)  — Return qty / sales qty

NOTES:
  - Date range in sales table spans from late 2020 to September 2025
  - ~10,000+ distinct SKUs
  - Always use LIMIT on large aggregations
"""


def make_sales_tools(db: DuckDBClient) -> list[Tool]:

    @tool
    def get_sales_schema() -> str:
        """
        Retrieve the schema and documentation for the Dealight retail sales tables.

        Returns:
            str: Full schema documentation including column descriptions and example queries.
        """
        _logger.info("Fetching sales schema")
        return _SCHEMA_DOC

    @tool(requires_approval=True)
    def query_sales(sql_query: str) -> Optional[str]:
        """
        Execute a SQL query against the Dealight sales database and return results.

        Args:
            sql_query (str): SQL query to execute against the sales or sku_profile tables.
        Returns:
            Optional[str]: Query results formatted as a text table, or an error message.
        """
        _logger.info("Executing sales query")
        return db.execute_query(sql_query)

    @tool
    def query_sales_and_visualize(query: str, spec: str) -> str:
        """
        Execute a SQL query against the sales database and return results embedded
        in a Vega-Lite spec ready for chart rendering on the frontend.

        On success returns JSON with type "chart". On failure returns type "query_error".

        Args:
            query (str): SQL query to execute against the sales or sku_profile tables.
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
                "suggestion": _sales_error_suggestion(result["error_type"]),
            })

        if not result["records"]:
            return json.dumps({
                "type": "query_error",
                "status": "NO_DATA",
                "reason": "Query executed successfully but returned no rows.",
                "error_type": "NO_DATA",
                "suggestion": "Try broadening the date range or removing filters.",
            })

        parsed_spec["data"] = {"values": result["records"]}
        return json.dumps({"type": "chart", "spec": parsed_spec})

    return [get_sales_schema, query_sales, query_sales_and_visualize]


def _sales_error_suggestion(error_type: str) -> str:
    suggestions = {
        "NO_DATA": "Broaden the date range or remove filters.",
        "SYNTAX_ERROR": "Review SQL syntax — check for missing commas or unsupported clauses.",
        "TABLE_NOT_FOUND": "Use table names: sales, sku_profile, forecasts. Call get_sales_schema() to confirm.",
        "COLUMN_NOT_FOUND": "Check column names against the schema. Call get_sales_schema() to list columns.",
        "QUERY_FAILED": "An unexpected error occurred. Check the reason field for details.",
    }
    return suggestions.get(error_type, "Check the query and try again.")
