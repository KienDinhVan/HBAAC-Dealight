from __future__ import annotations

import importlib
import sys
from types import ModuleType

from hbacc_prj.dataset_config import load_dataset_config


class FakeDAG:
    def __init__(self, dag_id, schedule=None, **kwargs):
        self.dag_id = dag_id
        self.schedule_interval = schedule
        self.kwargs = kwargs
        self.tasks = []


class FakeBashOperator:
    def __init__(self, dag, **kwargs):
        self.kwargs = kwargs
        dag.tasks.append(self)


def _import_factory(monkeypatch):
    airflow = ModuleType("airflow")
    airflow.DAG = FakeDAG
    operators = ModuleType("airflow.operators")
    bash = ModuleType("airflow.operators.bash")
    bash.BashOperator = FakeBashOperator
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.operators", operators)
    monkeypatch.setitem(sys.modules, "airflow.operators.bash", bash)
    sys.modules.pop("dags.factory", None)
    return importlib.import_module("dags.factory")


def test_builds_five_dags_for_hbaac(monkeypatch):
    factory = _import_factory(monkeypatch)
    cfg = load_dataset_config("datasets/hbaac_sku.yaml")
    dags = factory.build_dags_for_config(cfg)

    assert sorted(dags) == [
        "features_hbaac_sku",
        "forecast_hbaac_sku",
        "ingest_hbaac_sku",
        "monitor_hbaac_sku",
        "train_hbaac_sku",
    ]
    assert dags["ingest_hbaac_sku"].schedule_interval == "0 2 * * *"
    assert dags["train_hbaac_sku"].schedule_interval == "0 4 * * 0"
    task = dags["ingest_hbaac_sku"].tasks[0]
    assert dags["train_hbaac_sku"].kwargs["max_active_runs"] == 1
    assert (
        dags["train_hbaac_sku"].kwargs["dagrun_timeout"].total_seconds() == 45 * 60
    )
    assert task.kwargs["retries"] == 1
    assert task.kwargs["retry_delay"].total_seconds() == 60
    assert task.kwargs["execution_timeout"].total_seconds() == 30 * 60
    assert dags["ingest_hbaac_sku"].tasks[0].kwargs["cwd"].endswith(
        "HBAAC-Dealight"
    )
    assert "--dataset hbaac_sku" in dags["ingest_hbaac_sku"].tasks[0].kwargs[
        "bash_command"
    ]
