"""Per-dataset production-model cache with hot reload (Sprint 09).

get(dataset) loads models:/{dataset}-forecaster@production once and serves it
from RAM. invalidate(dataset) marks the entry stale; the next get() attempts a
reload and keeps the old model if the reload fails (load-then-swap).
The default dataset keeps the legacy MLFLOW_MODEL_URI / pickle fallback.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from api.app.clients.mlflow_loader import ModelLoadError, load_model

_logger = logging.getLogger(__name__)


@dataclass
class _Entry:
    model: Any
    stale: bool = False


class ModelCache:
    def __init__(
        self,
        tracking_uri: str,
        fallback_model_uri: str = "",
        fallback_path: str | None = None,
        default_dataset: str = "hbaac_sku",
    ) -> None:
        self.tracking_uri = tracking_uri
        self.fallback_model_uri = fallback_model_uri
        self.fallback_path = fallback_path
        self.default_dataset = default_dataset
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def _load_alias_model(self, dataset: str) -> Any | None:
        alias_uri = f"models:/{dataset}-forecaster@production"
        try:
            import mlflow  # type: ignore

            mlflow.set_tracking_uri(self.tracking_uri)
            _logger.info("Loading production model %s", alias_uri)
            return mlflow.pyfunc.load_model(alias_uri)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Alias load failed for %s (%s)", alias_uri, exc)
        if dataset == self.default_dataset and (
            self.fallback_model_uri or self.fallback_path
        ):
            try:
                return load_model(
                    self.tracking_uri, self.fallback_model_uri, self.fallback_path
                )
            except ModelLoadError:
                _logger.warning("Fallback load failed for %s", dataset)
        return None

    def get(self, dataset: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(dataset)
            if entry is not None and not entry.stale:
                return entry.model
        try:
            model = self._load_alias_model(dataset)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Model reload raised for %s (%s)", dataset, exc)
            model = None
        with self._lock:
            entry = self._entries.get(dataset)
            if model is not None:
                self._entries[dataset] = _Entry(model=model, stale=False)
                return model
            if entry is not None:
                # load-then-swap: keep serving the old model, stay stale to retry
                return entry.model
            # nothing loadable yet: retry on every call until a model appears
            return None

    def invalidate(self, dataset: str) -> None:
        with self._lock:
            entry = self._entries.get(dataset)
            if entry is not None:
                entry.stale = True
