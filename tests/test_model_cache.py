"""Tests for the per-dataset model cache (Sprint 09)."""
from __future__ import annotations

from unittest.mock import MagicMock

from api.app.clients.model_cache import ModelCache


def _cache(loader: MagicMock) -> ModelCache:
    cache = ModelCache("http://mlflow.test")
    cache._load_alias_model = loader  # inject
    return cache


def test_get_loads_once_and_caches() -> None:
    loader = MagicMock(return_value="model-v1")
    cache = _cache(loader)
    assert cache.get("ds") == "model-v1"
    assert cache.get("ds") == "model-v1"
    loader.assert_called_once_with("ds")


def test_invalidate_triggers_reload() -> None:
    loader = MagicMock(side_effect=["model-v1", "model-v2"])
    cache = _cache(loader)
    assert cache.get("ds") == "model-v1"
    cache.invalidate("ds")
    assert cache.get("ds") == "model-v2"
    assert loader.call_count == 2


def test_load_then_swap_keeps_old_model_on_failure() -> None:
    loader = MagicMock(side_effect=["model-v1", RuntimeError("boom"), "model-v2"])
    cache = _cache(loader)
    assert cache.get("ds") == "model-v1"
    cache.invalidate("ds")
    assert cache.get("ds") == "model-v1"  # reload failed -> old model survives
    assert cache.get("ds") == "model-v2"  # retried next call
    assert loader.call_count == 3


def test_no_model_available_returns_none() -> None:
    loader = MagicMock(return_value=None)
    cache = _cache(loader)
    assert cache.get("ds") is None
