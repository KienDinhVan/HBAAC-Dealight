import pandas as pd
import pytest

from hbacc_prj.connectors.registry import get_connector, ingest_dataset
from hbacc_prj.dataset_config import DatasetConfig, MappingConfig, SourceConfig


def _cfg(tmp_path, fmt="csv"):
    df = pd.DataFrame({"item": ["A", "B"], "day": ["2026-01-01", "2026-01-02"], "qty": [1, 2]})
    loc = tmp_path / f"s.{fmt}"
    (df.to_csv(loc, index=False) if fmt == "csv" else df.to_parquet(loc, index=False))
    return DatasetConfig(
        name="sample_ds",
        source=SourceConfig(type="file", location=str(loc), format=fmt),
        mapping=MappingConfig(entity_id="item", ds="day", quantity="qty"),
    )


def test_fetch_csv(tmp_path):
    fetch = get_connector("file")
    df = fetch(_cfg(tmp_path).source)
    assert df["item"].tolist() == ["A", "B"]


def test_fetch_parquet(tmp_path):
    df = get_connector("file")(_cfg(tmp_path, "parquet").source)
    assert len(df) == 2


def test_unknown_connector():
    with pytest.raises(KeyError):
        get_connector("ftp")


def test_ingest_dataset_end_to_end(tmp_path):
    out = ingest_dataset(_cfg(tmp_path))
    assert list(out.columns) == ["entity_id", "ds", "quantity", "attrs"]
    assert out["quantity"].tolist() == [1.0, 2.0]
