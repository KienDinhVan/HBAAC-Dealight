import pandas as pd
import pytest

from hbacc_prj.connectors.normalize import NormalizeError, normalize
from hbacc_prj.dataset_config import MappingConfig

MAP = MappingConfig(entity_id="item", ds="day", quantity="qty", attrs=["price"])


def test_normalize_happy_path():
    df = pd.DataFrame({"item": ["A", "B"], "day": ["2026-01-01", "2026-01-02"],
                       "qty": [3, 4.5], "price": [10.0, 20.0], "junk": [1, 2]})
    out = normalize(df, MAP)
    assert list(out.columns) == ["entity_id", "ds", "quantity", "attrs"]
    assert out["entity_id"].tolist() == ["A", "B"]
    assert str(out["ds"].dtype) == "datetime64[ns]"
    assert out["quantity"].tolist() == [3.0, 4.5]
    assert out["attrs"].iloc[0] == {"price": 10.0}


def test_missing_source_column():
    df = pd.DataFrame({"item": ["A"], "day": ["2026-01-01"]})
    with pytest.raises(NormalizeError) as e:
        normalize(df, MAP)
    assert any("qty" in r for r in e.value.report)


def test_unparseable_dates_reported():
    df = pd.DataFrame({"item": ["A"], "day": ["not-a-date"], "qty": [1], "price": [1.0]})
    with pytest.raises(NormalizeError) as e:
        normalize(df, MAP)
    assert any("ds" in r for r in e.value.report)


def test_duplicate_entity_ds_rejected():
    df = pd.DataFrame({"item": ["A", "A"], "day": ["2026-01-01"] * 2,
                       "qty": [1, 2], "price": [1.0, 1.0]})
    with pytest.raises(NormalizeError) as e:
        normalize(df, MAP)
    assert any("duplicate" in r for r in e.value.report)
