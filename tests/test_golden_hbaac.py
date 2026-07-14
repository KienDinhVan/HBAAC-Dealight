"""The canonical HBAAC path must preserve legacy daily net quantity."""
import pandas as pd

from hbacc_prj.connectors.registry import ingest_dataset
from hbacc_prj.data import load_train, make_daily_sales
from hbacc_prj.dataset_config import load_dataset_config

SAMPLE = "tests/load/train_sample.csv"


def test_canonical_matches_legacy_daily_net_qty():
    legacy = make_daily_sales(load_train(SAMPLE))
    cfg = load_dataset_config("datasets/hbaac_sku.yaml")
    object.__setattr__(cfg.source, "location", SAMPLE)
    canonical = ingest_dataset(cfg)

    merged = legacy.merge(
        canonical.rename(columns={"entity_id": "ItemCode", "ds": "Date"}),
        on=["ItemCode", "Date"],
        how="outer",
        indicator=True,
    )
    assert (merged["_merge"] == "both").all()
    pd.testing.assert_series_equal(
        merged["net_qty"].astype("float64"),
        merged["quantity"].astype("float64"),
        check_names=False,
    )
