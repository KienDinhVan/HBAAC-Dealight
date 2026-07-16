"""Tests for the MLflow registry wrapper (Sprint 09)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api.app.clients.mlflow_registry import ModelRegistryClient


def _mv(version: str, run_id: str, aliases: list[str]):
    return SimpleNamespace(
        version=version, run_id=run_id, creation_timestamp=int(version) * 1000,
        aliases=aliases,
    )


@pytest.fixture
def registry() -> ModelRegistryClient:
    reg = ModelRegistryClient("http://mlflow.test")
    client = MagicMock()
    client.search_model_versions.return_value = [
        _mv("1", "run-1", ["staging"]),
        _mv("2", "run-2", ["production"]),
    ]
    client.get_run.side_effect = lambda run_id: SimpleNamespace(
        data=SimpleNamespace(
            metrics={
                "lightgbm_wape": 0.5 if run_id == "run-1" else 0.6,
                "lightgbm_mae": 1.0,
                "lightgbm_rmse": 2.0,
                "lightgbm_smape": 0.3,
                "best_baseline_wape": 0.9,
            }
        )
    )
    client.get_registered_model.return_value = SimpleNamespace(
        aliases={"staging": "1", "production": "2"}
    )
    reg._client = client  # inject mock
    return reg


def test_aliases_merged_from_registered_model(registry: ModelRegistryClient) -> None:
    # MLflow 3.x search_model_versions returns empty aliases; the registered
    # model alias map must fill them in.
    registry._client.search_model_versions.return_value = [
        _mv("1", "run-1", []),
        _mv("2", "run-2", []),
    ]
    versions = {v["version"]: v["aliases"] for v in registry.list_versions("m")}
    assert versions == {"1": ["staging"], "2": ["production"]}


def test_list_versions_newest_first_with_metrics(registry: ModelRegistryClient) -> None:
    versions = registry.list_versions("m")
    assert [v["version"] for v in versions] == ["2", "1"]
    assert versions[1]["aliases"] == ["staging"]
    assert versions[1]["metrics"]["lightgbm_wape"] == 0.5
    assert "best_baseline_wape" not in versions[1]["metrics"]


def test_get_alias_version(registry: ModelRegistryClient) -> None:
    assert registry.get_alias_version("m", "production") == "2"
    assert registry.get_alias_version("m", "champion") is None


def test_compare(registry: ModelRegistryClient) -> None:
    result = registry.compare("m", "1")
    assert result["candidate"]["version"] == "1"
    assert result["production"]["version"] == "2"


def test_compare_missing_candidate_raises(registry: ModelRegistryClient) -> None:
    with pytest.raises(ValueError):
        registry.compare("m", "99")


def test_promote_flips_aliases(registry: ModelRegistryClient) -> None:
    old = registry.promote("m", "1")
    assert old == "2"
    calls = registry._client.set_registered_model_alias.call_args_list
    assert calls[0].args == ("m", "production", "1")
    assert calls[1].args == ("m", "staging", "2")


def test_promote_idempotent_when_already_production(registry: ModelRegistryClient) -> None:
    assert registry.promote("m", "2") == "2"
    registry._client.set_registered_model_alias.assert_not_called()


def test_promote_first_time_no_old_production(registry: ModelRegistryClient) -> None:
    registry._client.search_model_versions.return_value = [_mv("1", "run-1", [])]
    assert registry.promote("m", "1") is None
    registry._client.set_registered_model_alias.assert_called_once_with(
        "m", "production", "1"
    )
