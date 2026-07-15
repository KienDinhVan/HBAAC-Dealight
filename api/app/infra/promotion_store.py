"""Postgres store for model promotion requests (Sprint 09)."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_COLUMNS = (
    "id, dataset, model_name, candidate_version, current_prod_version,"
    " metrics_snapshot, requested_by, request_note, status, reviewed_by,"
    " review_comment, created_at, reviewed_at"
)


class PromotionStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def create_request(
        self,
        *,
        dataset: str,
        model_name: str,
        candidate_version: str,
        current_prod_version: str | None,
        metrics_snapshot: dict[str, Any],
        requested_by: str,
        request_note: str | None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO mlops.promotion_requests
                        (dataset, model_name, candidate_version, current_prod_version,
                         metrics_snapshot, requested_by, request_note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_COLUMNS}
                    """,
                    (
                        dataset, model_name, candidate_version, current_prod_version,
                        Jsonb(metrics_snapshot), requested_by, request_note,
                    ),
                )
                return cursor.fetchone()  # type: ignore[return-value]

    def has_pending(self, dataset: str, candidate_version: str) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM mlops.promotion_requests"
                    " WHERE dataset = %s AND candidate_version = %s"
                    " AND status = 'pending' LIMIT 1",
                    (dataset, candidate_version),
                )
                return cursor.fetchone() is not None

    def list_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        query = f"SELECT {_COLUMNS} FROM mlops.promotion_requests"
        params: list[Any] = []
        if status:
            query += " WHERE status = %s"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT 200"
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return list(cursor.fetchall())

    def get(self, request_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {_COLUMNS} FROM mlops.promotion_requests WHERE id = %s",
                    (request_id,),
                )
                return cursor.fetchone()

    def mark_reviewed(
        self, request_id: int, status: str, reviewed_by: str, comment: str | None
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE mlops.promotion_requests
                    SET status = %s, reviewed_by = %s, review_comment = %s,
                        reviewed_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING {_COLUMNS}
                    """,
                    (status, reviewed_by, comment, request_id),
                )
                return cursor.fetchone()
