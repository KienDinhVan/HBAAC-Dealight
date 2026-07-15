"""Seed dev/manager users (idempotent upsert).

Env: DATABASE_URL, SEED_DEV_PASSWORD, SEED_MANAGER_PASSWORD.
Run inside the forecast-api pod or any env with DB access:
    python scripts/seed_users.py
"""
from __future__ import annotations

import os
import sys

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api.app.auth import hash_password  # noqa: E402

SEEDS = [
    ("dev1", "SEED_DEV_PASSWORD", "dev"),
    ("manager1", "SEED_MANAGER_PASSWORD", "manager"),
]


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for username, env_name, role in SEEDS:
                password = os.environ.get(env_name)
                if not password:
                    print(f"skip {username}: {env_name} not set")
                    continue
                cursor.execute(
                    """
                    INSERT INTO mlops.users (username, password_hash, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (username)
                    DO UPDATE SET password_hash = EXCLUDED.password_hash,
                                  role = EXCLUDED.role
                    """,
                    (username, hash_password(password), role),
                )
                print(f"seeded {username} ({role})")


if __name__ == "__main__":
    main()
