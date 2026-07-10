from __future__ import annotations

from datetime import datetime
from typing import Any


class OfflineStoreClient:
    """Read-only stats over the BigQuery offline store (Iceberg `sales_daily`).

    The BigQuery client is created lazily so app startup and unit tests never
    touch the network.
    """

    def __init__(self, project: str, dataset: str, table: str = "sales_daily") -> None:
        self._project = project
        self._table_id = f"{project}.{dataset}.{table}"
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self._project)
        return self._client

    def batch_stats(self, as_of: datetime | None = None) -> list[dict[str, Any]]:
        """Row counts per batch, optionally at an Iceberg time-travel snapshot."""
        from google.cloud import bigquery

        time_travel = "FOR SYSTEM_TIME AS OF @as_of " if as_of else ""
        sql = (
            "SELECT batch_id, COUNT(*) AS row_count, "
            "CAST(MIN(date) AS STRING) AS min_date, "
            "CAST(MAX(date) AS STRING) AS max_date, "
            "CAST(MAX(loaded_at) AS STRING) AS loaded_at "
            f"FROM `{self._table_id}` {time_travel}"
            "GROUP BY batch_id ORDER BY loaded_at DESC"
        )
        parameters = (
            [bigquery.ScalarQueryParameter("as_of", "TIMESTAMP", as_of)] if as_of else []
        )
        job = self._get_client().query(
            sql, job_config=bigquery.QueryJobConfig(query_parameters=parameters)
        )
        return [dict(row) for row in job.result()]
