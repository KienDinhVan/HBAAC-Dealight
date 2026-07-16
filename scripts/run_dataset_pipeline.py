"""Per-dataset pipeline runner used by factory-generated Airflow DAGs."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
from typing import Any

from hbacc_prj.connectors.registry import ingest_dataset
from hbacc_prj.dataset_config import DatasetConfig, load_dataset_config

STAGES = ("ingest", "features", "train", "forecast", "monitor")


def _post_discord(webhook_url: str, content: str) -> None:
    import urllib.request

    request = urllib.request.Request(
        webhook_url,
        data=json.dumps({"content": content}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def _notify_training_success(
    dataset: str, batch_id: str, report: dict[str, Any]
) -> None:
    """Send a best-effort Discord summary after training fully completes."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("INFO: DISCORD_WEBHOOK_URL is not configured; skipping retrain alert")
        return

    model_metrics = report.get("metrics", {}).get("lightgbm", {})
    model_name = report.get("registered_model_name") or "not registered"
    model_version = report.get("registered_model_version") or "n/a"
    quality_gate = "PASSED" if report.get("passed_registration_rule") else "FAILED"
    message = (
        "**[Model retrain] SUCCESS**\n"
        f"- Dataset: `{dataset}`\n"
        f"- Airflow run: `{batch_id}`\n"
        f"- Quality gate: **{quality_gate}**\n"
        f"- LightGBM WAPE: `{model_metrics.get('wape', 'n/a')}`\n"
        f"- Best baseline WAPE: `{report.get('best_baseline_wape', 'n/a')}`\n"
        f"- Model: `{model_name}` version `{model_version}` (`@staging`)\n"
        f"- MLflow run: `{report.get('mlflow_run_id', 'n/a')}`"
    )
    try:
        _post_discord(webhook_url, message)
        print(f"Discord retrain alert sent for {dataset} run {batch_id}")
    except Exception as exc:  # noqa: BLE001 - alerting must not fail training
        print(f"WARNING: Discord retrain alert failed: {exc}")


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


def run_stage(cfg: DatasetConfig, stage: str, batch_id: str) -> dict[str, Any] | None:
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

        report = train_for_dataset(cfg)
        _notify_training_success(cfg.name, batch_id, report)
        return report
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
