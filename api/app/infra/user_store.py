"""Postgres-backed user lookup for auth (Sprint 09)."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row


class UserStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def get_user(self, username: str) -> dict[str, Any] | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT username, password_hash, role FROM mlops.users"
                    " WHERE username = %s",
                    (username,),
                )
                return cursor.fetchone()
