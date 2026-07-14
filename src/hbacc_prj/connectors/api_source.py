"""REST connector with optional bearer authentication and pagination."""
from __future__ import annotations

import os

import pandas as pd
import requests

from hbacc_prj.dataset_config import SourceConfig

MAX_PAGES = 1000


def fetch(source: SourceConfig) -> pd.DataFrame:
    headers: dict[str, str] = {}
    if source.secret_ref:
        if source.secret_ref not in os.environ:
            raise KeyError(f"env var '{source.secret_ref}' (API key) not set")
        headers["Authorization"] = f"Bearer {os.environ[source.secret_ref]}"

    records: list[dict] = []
    url: str | None = source.location
    params = dict(source.params)
    for _ in range(MAX_PAGES):
        if not url:
            break
        response = requests.get(url, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        page = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(page, list):
            raise ValueError("API response must be a list or contain a 'results' list")
        records.extend(page)
        url = payload.get("next") if isinstance(payload, dict) else None
        params = {}
    else:
        raise RuntimeError(f"API pagination exceeded {MAX_PAGES} pages")
    return pd.DataFrame.from_records(records)
