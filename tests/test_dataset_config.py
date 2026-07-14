from pathlib import Path

import pytest

from hbacc_prj.dataset_config import ConfigError, DatasetConfig, load_all_dataset_configs, load_dataset_config

VALID = """
name: sample_ds
source:
  type: file
  location: data/raw/sample.csv
  format: csv
mapping:
  entity_id: item
  ds: day
  quantity: qty
  attrs: [price]
schedule: "0 2 * * *"
training:
  schedule: "0 4 * * 0"
  validation_days: 28
  min_wape_improvement: 0.02
"""


def _write(tmp_path: Path, text: str, name: str = "sample_ds.yaml") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def test_load_valid_config(tmp_path):
    cfg = load_dataset_config(_write(tmp_path, VALID))
    assert cfg.name == "sample_ds"
    assert cfg.source.type == "file"
    assert cfg.mapping.entity_id == "item"
    assert cfg.mapping.attrs == ["price"]
    assert cfg.training.validation_days == 28
    assert cfg.postprocess is None


def test_bad_name_rejected(tmp_path):
    with pytest.raises(ConfigError, match="name"):
        load_dataset_config(_write(tmp_path, VALID.replace("sample_ds", "Bad-Name!")))


def test_missing_mapping_field_rejected(tmp_path):
    broken = VALID.replace("  quantity: qty\n", "")
    with pytest.raises(ConfigError, match="quantity"):
        load_dataset_config(_write(tmp_path, broken))


def test_unknown_source_type_rejected(tmp_path):
    with pytest.raises(ConfigError, match="source.type"):
        load_dataset_config(_write(tmp_path, VALID.replace("type: file", "type: ftp")))


def test_load_all_skips_invalid(tmp_path, caplog):
    _write(tmp_path, VALID)
    _write(tmp_path, "name: [broken", name="broken.yaml")
    configs = load_all_dataset_configs(tmp_path)
    assert [c.name for c in configs] == ["sample_ds"]
    assert "broken.yaml" in caplog.text
