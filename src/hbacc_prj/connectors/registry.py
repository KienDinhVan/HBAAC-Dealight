"""Connector dispatch + one-call dataset ingest."""
from __future__ import annotations

from typing import Callable

import pandas as pd

from hbacc_prj import hooks
from hbacc_prj.connectors import api_source, database_source, file_source
from hbacc_prj.connectors.normalize import normalize
from hbacc_prj.dataset_config import DatasetConfig, SourceConfig

_CONNECTORS: dict[str, Callable[[SourceConfig], pd.DataFrame]] = {
    "file": file_source.fetch,
    "database": database_source.fetch,
    "api": api_source.fetch,
}


def get_connector(source_type: str) -> Callable[[SourceConfig], pd.DataFrame]:
    if source_type not in _CONNECTORS:
        raise KeyError(f"no connector for source type '{source_type}'")
    return _CONNECTORS[source_type]


def register_connector(source_type: str, fetch: Callable[[SourceConfig], pd.DataFrame]) -> None:
    _CONNECTORS[source_type] = fetch


def ingest_dataset(cfg: DatasetConfig) -> pd.DataFrame:
    raw = get_connector(cfg.source.type)(cfg.source)
    if cfg.preprocess:
        raw = hooks.get_hook(cfg.preprocess)(raw)
    return normalize(raw, cfg.mapping)
