import pandas as pd

from scripts.run_dataset_pipeline import run_stage
from hbacc_prj.dataset_config import DatasetConfig, MappingConfig, SourceConfig


def test_ingest_stage_writes_parquet(tmp_path, monkeypatch):
    src = tmp_path / "s.csv"
    pd.DataFrame({"item": ["A"], "day": ["2026-01-01"], "qty": [1]}).to_csv(src, index=False)
    cfg = DatasetConfig(
        name="sample_ds",
        source=SourceConfig(type="file", location=str(src)),
        mapping=MappingConfig(entity_id="item", ds="day", quantity="qty"),
    )
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))  # write under tmp instead of gs://
    run_stage(cfg, "ingest", batch_id="b1")
    out = pd.read_parquet(tmp_path / "raw" / "sample_ds" / "b1" / "canonical.parquet")
    assert out["entity_id"].tolist() == ["A"]
