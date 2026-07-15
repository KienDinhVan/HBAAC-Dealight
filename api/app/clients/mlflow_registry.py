"""Thin wrapper over the MLflow model registry (aliases, MLflow 3.x).

Only the metric keys logged by train_and_log are exposed to the UI.
"""
from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

METRIC_KEYS = ("lightgbm_wape", "lightgbm_mae", "lightgbm_rmse", "lightgbm_smape")


class ModelRegistryClient:
    def __init__(self, tracking_uri: str) -> None:
        self.tracking_uri = tracking_uri
        self._client: Any = None  # lazy; tests inject a mock

    def _mlflow(self) -> Any:
        if self._client is None:
            from mlflow.tracking import MlflowClient

            self._client = MlflowClient(tracking_uri=self.tracking_uri)
        return self._client

    def _run_metrics(self, run_id: str) -> dict[str, float]:
        try:
            metrics = self._mlflow().get_run(run_id).data.metrics
        except Exception:  # noqa: BLE001
            _logger.warning("Could not read run metrics for %s", run_id)
            return {}
        return {key: float(metrics[key]) for key in METRIC_KEYS if key in metrics}

    def list_versions(self, model_name: str) -> list[dict[str, Any]]:
        raw = self._mlflow().search_model_versions(f"name = '{model_name}'")
        versions = [
            {
                "version": str(mv.version),
                "run_id": mv.run_id,
                "created_at": int(mv.creation_timestamp),
                "aliases": list(mv.aliases or []),
                "metrics": self._run_metrics(mv.run_id) if mv.run_id else {},
            }
            for mv in raw
        ]
        versions.sort(key=lambda item: int(item["version"]), reverse=True)
        return versions

    def get_alias_version(self, model_name: str, alias: str) -> str | None:
        for version in self.list_versions(model_name):
            if alias in version["aliases"]:
                return version["version"]
        return None

    def compare(self, model_name: str, candidate_version: str) -> dict[str, Any]:
        versions = {v["version"]: v for v in self.list_versions(model_name)}
        candidate = versions.get(str(candidate_version))
        if candidate is None:
            raise ValueError(
                f"version {candidate_version} not found for model {model_name}"
            )
        production = next(
            (v for v in versions.values() if "production" in v["aliases"]), None
        )
        return {"candidate": candidate, "production": production}

    def promote(self, model_name: str, candidate_version: str) -> str | None:
        candidate_version = str(candidate_version)
        old = self.get_alias_version(model_name, "production")
        if old == candidate_version:
            return old
        client = self._mlflow()
        client.set_registered_model_alias(model_name, "production", candidate_version)
        if old is not None:
            client.set_registered_model_alias(model_name, "staging", old)
        return old
