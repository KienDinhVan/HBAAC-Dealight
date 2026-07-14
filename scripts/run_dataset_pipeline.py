"""Per-dataset pipeline runner used by factory-generated Airflow DAGs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from hbacc_prj.connectors.registry import ingest_dataset
from hbacc_prj.dataset_config import DatasetConfig, load_dataset_config

STAGES = ("ingest", "features", "train", "forecast", "monitor")


def _data_root() -> str:
    return os.environ.get("DATA_ROOT", os.environ.get("GCS_DATA_ROOT", "data"))


def run_stage(cfg: DatasetConfig, stage: str, batch_id: str) -> None:
    if stage == "ingest":
        canonical = ingest_dataset(cfg)
        # normalize() emits `attrs` as a dict-per-row column; when a dataset
        # has no configured attrs columns every row is `{}`, which PyArrow
        # cannot write as a zero-field struct. JSON-encode it so parquet
        # writing is safe regardless of whether attrs are configured.
        canonical = canonical.assign(attrs=canonical["attrs"].map(json.dumps))
        dest = Path(_data_root()) / "raw" / cfg.name / batch_id
        dest.mkdir(parents=True, exist_ok=True)
        canonical.to_parquet(dest / "canonical.parquet", index=False)
        return
    if stage == "features":
        from hbacc_prj.features import build_features_for_dataset
        build_features_for_dataset(cfg, batch_id)
        return
    if stage == "train":
        from hbacc_prj.training import train_for_dataset
        train_for_dataset(cfg)
        return
    if stage == "forecast":
        from hbacc_prj.forecasting import forecast_for_dataset
        forecast_for_dataset(cfg)
        return
    if stage == "monitor":
        from hbacc_prj.monitoring import monitor_dataset
        monitor_dataset(cfg)
        return
    raise ValueError(f"unknown stage {stage}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--stage", required=True, choices=STAGES)
    ap.add_argument("--batch-id", default="manual")
    args = ap.parse_args()
    cfg = load_dataset_config(Path("datasets") / f"{args.dataset}.yaml")
    run_stage(cfg, args.stage, args.batch_id)


if __name__ == "__main__":
    main()
