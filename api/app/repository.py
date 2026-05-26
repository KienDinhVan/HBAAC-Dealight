from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


class ForecastRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection

    def ping(self) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() is not None

    def latest_run(self, forecast_date: date | None = None) -> dict[str, Any] | None:
        query = """
            SELECT run_id, forecast_date, model_name, model_version, status, row_count,
                   started_at, finished_at, error_message
            FROM serving.forecast_runs
            WHERE status = 'success'
        """
        params: list[Any] = []
        if forecast_date is not None:
            query += " AND forecast_date = %s"
            params.append(forecast_date)
        query += " ORDER BY finished_at DESC NULLS LAST, started_at DESC LIMIT 1"
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()

    def forecast(
        self,
        item_code: str,
        days: int,
        forecast_date: date | None = None,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        run = self.latest_run(forecast_date)
        if run is None:
            return None, []
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT target_date, horizon, predicted_quantity
                    FROM serving.sku_forecast
                    WHERE run_id = %s AND item_code = %s AND horizon <= %s
                    ORDER BY horizon
                    """,
                    (run["run_id"], item_code, days),
                )
                return run, list(cursor.fetchall())

    def top_skus(
        self, target_date: date, limit: int
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        run = self.latest_run()
        if run is None:
            return None, []
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT item_code, target_date, horizon, predicted_quantity
                    FROM serving.sku_forecast
                    WHERE run_id = %s AND target_date = %s
                    ORDER BY predicted_quantity DESC, item_code
                    LIMIT %s
                    """,
                    (run["run_id"], target_date, limit),
                )
                return run, list(cursor.fetchall())

    def summary(
        self, target_date: date
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        run = self.latest_run()
        if run is None:
            return None, None
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT target_date,
                           COUNT(*) AS sku_count,
                           SUM(predicted_quantity) AS total_predicted_quantity,
                           AVG(predicted_quantity) AS avg_predicted_quantity,
                           MAX(predicted_quantity) AS max_predicted_quantity
                    FROM serving.sku_forecast
                    WHERE run_id = %s AND target_date = %s
                    GROUP BY target_date
                    """,
                    (run["run_id"], target_date),
                )
                return run, cursor.fetchone()
