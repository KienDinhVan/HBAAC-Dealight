"""Generate per-dataset Airflow DAGs from datasets/*.yaml."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator

from hbacc_prj.dataset_config import DatasetConfig, load_all_dataset_configs

DATASETS_DIR = Path(os.environ.get("DATASETS_DIR", "datasets"))
PROJECT_ROOT = Path(
    os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1])
)
CMD = (
    "python -m scripts.run_dataset_pipeline "
    "--dataset {name} --stage {stage} --batch-id '{{{{ run_id }}}}'"
)


def _dag(dag_id: str, schedule: str | None, cfg: DatasetConfig, stage: str) -> DAG:
    dag = DAG(
        dag_id=dag_id,
        start_date=datetime(2026, 1, 1),
        schedule=schedule,
        catchup=False,
        # LocalExecutor: concurrent runs of the same stage fight over the
        # scheduler pod's CPU (3 parallel trains ~3x slower each).
        max_active_runs=1,
        tags=["dataset", cfg.name],
    )
    BashOperator(
        task_id=stage,
        bash_command=CMD.format(name=cfg.name, stage=stage),
        cwd=str(PROJECT_ROOT),
        dag=dag,
        retries=2,
    )
    return dag


def build_dags_for_config(cfg: DatasetConfig) -> dict[str, DAG]:
    """Build independently triggerable stage DAGs for one dataset."""
    return {
        f"ingest_{cfg.name}": _dag(
            f"ingest_{cfg.name}", cfg.schedule, cfg, "ingest"
        ),
        f"features_{cfg.name}": _dag(
            f"features_{cfg.name}", None, cfg, "features"
        ),
        f"train_{cfg.name}": _dag(
            f"train_{cfg.name}", cfg.training.schedule, cfg, "train"
        ),
        f"forecast_{cfg.name}": _dag(
            f"forecast_{cfg.name}", None, cfg, "forecast"
        ),
        f"monitor_{cfg.name}": _dag(
            f"monitor_{cfg.name}", None, cfg, "monitor"
        ),
    }


for _cfg in load_all_dataset_configs(DATASETS_DIR):
    for _dag_id, _dag_obj in build_dags_for_config(_cfg).items():
        globals()[_dag_id] = _dag_obj
