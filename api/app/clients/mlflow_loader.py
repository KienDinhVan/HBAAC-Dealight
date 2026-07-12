"""Lazy loader for the production LightGBM model — used by inline /predict/csv.

Strategy:
  1. If MLFLOW_MODEL_URI is set, load via `mlflow.pyfunc.load_model(uri)`.
  2. Else if PRODUCTION_SUBMISSION_PATH points to a pickle, load it.
  3. Otherwise raise ModelLoadError.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


class ModelLoadError(RuntimeError):
    pass


def load_model(mlflow_tracking_uri: str, model_uri: str, fallback_path: str | None = None) -> Any:
    if model_uri:
        try:
            import mlflow  # type: ignore

            mlflow.set_tracking_uri(mlflow_tracking_uri)
            _logger.info("Loading MLflow model: %s (tracking=%s)", model_uri, mlflow_tracking_uri)
            return mlflow.pyfunc.load_model(model_uri)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("MLflow load failed (%s) — will try local fallback.", exc)

    if fallback_path:
        path = Path(fallback_path)
        if path.exists() and path.suffix in {".pkl", ".joblib"}:
            try:
                import joblib  # type: ignore

                _logger.info("Loading local model pickle: %s", path)
                return joblib.load(path)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("Local model load failed (%s).", exc)

    raise ModelLoadError(
        "No model artifact available — set MLFLOW_MODEL_URI or PRODUCTION_SUBMISSION_PATH to a pickle."
    )
