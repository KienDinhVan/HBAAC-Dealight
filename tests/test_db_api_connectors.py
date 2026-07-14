import pandas as pd
import pytest

from hbacc_prj.connectors import api_source, database_source
from hbacc_prj.connectors.registry import get_connector, ingest_dataset
from hbacc_prj.dataset_config import SourceConfig, load_dataset_config


def test_database_fetch_sqlite(tmp_path, monkeypatch):
    import sqlalchemy as sa

    database = tmp_path / "test.db"
    engine = sa.create_engine(f"sqlite:///{database}")
    pd.DataFrame(
        {"item": ["A"], "day": ["2026-01-01"], "qty": [2]}
    ).to_sql("sales", engine, index=False)
    engine.dispose()
    monkeypatch.setenv("TEST_DSN", f"sqlite:///{database}")

    source = SourceConfig(
        type="database", secret_ref="TEST_DSN", query="SELECT * FROM sales"
    )
    assert database_source.fetch(source)["qty"].tolist() == [2]


def test_database_missing_secret():
    source = SourceConfig(
        type="database", secret_ref="NOPE_DSN", query="SELECT 1"
    )
    with pytest.raises(KeyError, match="NOPE_DSN"):
        database_source.fetch(source)


def test_api_fetch_with_pagination(monkeypatch):
    pages = {
        "http://x/data": {
            "results": [{"item": "A", "qty": 1}],
            "next": "http://x/data?page=2",
        },
        "http://x/data?page=2": {
            "results": [{"item": "B", "qty": 2}],
            "next": None,
        },
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    monkeypatch.setattr(
        api_source.requests,
        "get",
        lambda url, headers=None, params=None, timeout=None: FakeResponse(pages[url]),
    )
    source = SourceConfig(type="api", location="http://x/data")
    assert api_source.fetch(source)["item"].tolist() == ["A", "B"]


def test_registry_contains_all_connectors():
    assert all(get_connector(kind) for kind in ("file", "database", "api"))


def test_sample_shop_ingests_with_canonical_schema():
    cfg = load_dataset_config("datasets/sample_shop.yaml")
    output = ingest_dataset(cfg)
    assert len(output) == 20
    assert output.columns.tolist() == ["entity_id", "ds", "quantity", "attrs"]
    assert output["entity_id"].nunique() == 2
