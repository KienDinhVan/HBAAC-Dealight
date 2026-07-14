import pandas as pd

from hbacc_prj.connectors.registry import ingest_dataset
from hbacc_prj.dataset_config import load_dataset_config


def test_hbaac_yaml_loads_and_ingests(tmp_path):
    cfg = load_dataset_config("datasets/hbaac_sku.yaml")
    assert cfg.name == "hbaac_sku"
    assert cfg.table_name == "sales_daily"  # backward compat

    # tiny synthetic raw file in HBAAC source format
    raw = pd.DataFrame({
        "ItemCode": ["S1", "S1", "S2"],
        "Date": ["2026-01-01", "2026-01-01", "2026-01-02"],
        "Quantity": [2, -1, 5],
        "SalesAmount": ["10,5", "-5,0", "20,0"],   # VN decimal comma
        "Cost Amount": ["8,0", "-4,0", "15,0"],
        "UnitPrice": ["5,25", "5,0", "4,0"],
        "Unit Cost": ["4,0", "4,0", "3,0"],
    })
    p = tmp_path / "train.csv"
    raw.to_csv(p, index=False)
    object.__setattr__(cfg.source, "location", str(p))

    out = ingest_dataset(cfg)
    assert list(out.columns) == ["entity_id", "ds", "quantity", "attrs"]
    # S1 on 2026-01-01: net 2 + (-1) = 1 after daily aggregation in the hook
    s1 = out[out["entity_id"] == "S1"]
    assert s1["quantity"].iloc[0] == 1.0
