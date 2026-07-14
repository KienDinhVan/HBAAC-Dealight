"""Per-dataset pipeline runner used by factory-generated Airflow DAGs."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path

from hbacc_prj.connectors.registry import ingest_dataset
from hbacc_prj.dataset_config import DatasetConfig, load_dataset_config

STAGES = ("ingest", "features", "train", "forecast", "monitor")


def _data_root() -> str:
    if root := os.environ.get("DATA_ROOT"):
        return root
    if root := os.environ.get("GCS_DATA_ROOT"):
        return root
    if bucket := os.environ.get("GCS_BUCKET"):
        return f"gs://{bucket}"
    return "data"


def _write_canonical(canonical, dataset: str, batch_id: str) -> None:
    root = _data_root().rstrip("/")
    relative = f"raw/{dataset}/{batch_id}/canonical.parquet"
    if root.startswith("gs://"):
        from google.cloud import storage

        bucket_and_prefix = root.removeprefix("gs://")
        bucket, _, prefix = bucket_and_prefix.partition("/")
        blob_name = "/".join(part for part in (prefix, relative) if part)
        payload = io.BytesIO()
        canonical.to_parquet(payload, index=False)
        payload.seek(0)
        storage.Client().bucket(bucket).blob(blob_name).upload_from_file(
            payload, content_type="application/octet-stream"
        )
        return

    destination = Path(root) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    canonical.to_parquet(destination, index=False)


def run_stage(cfg: DatasetConfig, stage: str, batch_id: str) -> None:
    if stage == "ingest":
        canonical = ingest_dataset(cfg)
        # normalize() emits `attrs` as a dict-per-row column; when a dataset
        # has no configured attrs columns every row is `{}`, which PyArrow
        # cannot write as a zero-field struct. JSON-encode it so parquet
        # writing is safe regardless of whether attrs are configured.
        canonical = canonical.assign(attrs=canonical["attrs"].map(json.dumps))
        _write_canonical(canonical, cfg.name, batch_id)
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
    datasets_dir = Path(os.environ.get("DATASETS_DIR", "datasets"))
    cfg = load_dataset_config(datasets_dir / f"{args.dataset}.yaml")
    run_stage(cfg, args.stage, args.batch_id)


if __name__ == "__main__":
    main()
