"""One-off: give each dataset model a @production alias if it has none.

Assigns @production to the newest registered version of {dataset}-forecaster
for every datasets/*.yaml. Safe to re-run (skips models that already have it).
Env: MLFLOW_TRACKING_URI, optional DATASETS_DIR (default: datasets).
"""
from __future__ import annotations

import os
from pathlib import Path

from mlflow.tracking import MlflowClient

from hbacc_prj.dataset_config import load_all_dataset_configs


def main() -> None:
    tracking_uri = os.environ["MLFLOW_TRACKING_URI"]
    client = MlflowClient(tracking_uri=tracking_uri)
    directory = Path(os.environ.get("DATASETS_DIR", "datasets"))
    for config in load_all_dataset_configs(directory):
        model_name = f"{config.name}-forecaster"
        versions = client.search_model_versions(f"name = '{model_name}'")
        if not versions:
            print(f"{model_name}: no versions registered — skip")
            continue
        if any("production" in (v.aliases or []) for v in versions):
            print(f"{model_name}: @production already set — skip")
            continue
        newest = max(versions, key=lambda v: int(v.version))
        client.set_registered_model_alias(model_name, "production", newest.version)
        print(f"{model_name}: @production -> v{newest.version}")


if __name__ == "__main__":
    main()
