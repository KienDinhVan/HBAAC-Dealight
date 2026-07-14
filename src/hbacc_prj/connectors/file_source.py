"""File connector: CSV/Parquet from local path or GCS URI (gs://...)."""
from __future__ import annotations

import pandas as pd

from hbacc_prj.dataset_config import SourceConfig


def fetch(source: SourceConfig) -> pd.DataFrame:
    if source.format == "parquet":
        return pd.read_parquet(source.location)
    if source.format == "csv":
        return pd.read_csv(source.location, low_memory=False)
    raise ValueError(f"unsupported file format: {source.format}")
