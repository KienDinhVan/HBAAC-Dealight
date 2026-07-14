"""Database connector: resolve a DSN from the environment and execute SQL."""
from __future__ import annotations

import os

import pandas as pd
import sqlalchemy as sa

from hbacc_prj.dataset_config import SourceConfig


def fetch(source: SourceConfig) -> pd.DataFrame:
    if source.secret_ref not in os.environ:
        raise KeyError(f"env var '{source.secret_ref}' (database DSN) not set")
    engine = sa.create_engine(os.environ[source.secret_ref])
    try:
        with engine.connect() as connection:
            return pd.read_sql(sa.text(source.query), connection)
    finally:
        engine.dispose()
