from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import lightgbm as lgb
import mlflow
import mlflow.lightgbm
import numpy as np
import pandas as pd
import psycopg
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient

from hbacc_prj.dataset_config import DatasetConfig
from hbacc_prj.features import NUMERIC_FEATURE_COLUMNS, TIME_FEATURE_COLUMNS

MODEL_NAME = "sku-demand-lightgbm"
MODEL_FEATURE_COLUMNS = (
    TIME_FEATURE_COLUMNS + NUMERIC_FEATURE_COLUMNS + ["item_code_id"]
)


def calculate_wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denominator = float(np.abs(actual).sum())
    return float(np.abs(actual - predicted).sum() / max(denominator, 1e-9))


def calculate_smape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denominator = np.abs(actual) + np.abs(predicted)
    ratio = np.divide(
        2.0 * np.abs(actual - predicted),
        denominator,
        out=np.zeros_like(actual, dtype="float64"),
        where=denominator > 0,
    )
    return float(ratio.mean())


def clip_negative_predictions(predicted: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(predicted, dtype="float64"), 0.0, None)


def metric_set(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = actual - predicted
    return {
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt(np.square(error).mean())),
        "wape": calculate_wape(actual, predicted),
        "smape": calculate_smape(actual, predicted),
    }


def time_based_split(
    frame: pd.DataFrame, validation_days: int = 28
) -> tuple[pd.DataFrame, pd.DataFrame, date]:
    dates = pd.to_datetime(frame["target_date"])
    validation_start = dates.max() - pd.Timedelta(days=validation_days - 1)
    train = frame.loc[dates < validation_start].copy()
    validation = frame.loc[dates >= validation_start].copy()
    if train.empty or validation.empty:
        raise ValueError("Time split produced an empty train or validation set")
    return train, validation, validation_start.date()


def read_features(
    connection: psycopg.Connection[Any], feature_version: str
) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM features.offline_sku_features
            WHERE feature_version = %s
            ORDER BY target_date, item_code, horizon, as_of_date
            """,
            (feature_version,),
        )
        columns = [description.name for description in cursor.description or []]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def build_lgb_dataset(
    train: pd.DataFrame, validation: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    all_skus = sorted(set(train["item_code"]) | set(validation["item_code"]))
    mapping = {sku: index for index, sku in enumerate(all_skus)}

    def transform(frame: pd.DataFrame) -> pd.DataFrame:
        x = frame[TIME_FEATURE_COLUMNS + NUMERIC_FEATURE_COLUMNS].copy()
        for column in TIME_FEATURE_COLUMNS:
            x[column] = x[column].astype("int8")
        x[NUMERIC_FEATURE_COLUMNS] = (
            x[NUMERIC_FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        )
        x["item_code_id"] = frame["item_code"].map(mapping).astype("int32")
        return x[MODEL_FEATURE_COLUMNS]

    return (
        transform(train),
        transform(validation),
        train["target_quantity"].astype("float64"),
        validation["target_quantity"].astype("float64"),
    )


def fit_lightgbm(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_validation: pd.DataFrame,
    y_validation: pd.Series,
    random_seed: int = 2026,
) -> tuple[lgb.LGBMRegressor, np.ndarray]:
    model = lgb.LGBMRegressor(
        objective="regression_l1",
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.9,
        colsample_bytree=0.85,
        random_state=random_seed,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(
        x_train,
        y_train,
        eval_set=[(x_validation, y_validation)],
        callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)],
    )
    return model, clip_negative_predictions(model.predict(x_validation))


def baseline_predictions(validation: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
        "naive_last_value": clip_negative_predictions(validation["lag_1"].to_numpy()),
        "seasonal_naive_lag_7": clip_negative_predictions(
            validation["lag_7"].to_numpy()
        ),
        "moving_average_28": clip_negative_predictions(
            validation["rolling_mean_28"].to_numpy()
        ),
    }


def _verify_logged_model(
    model_uri: str, x_validation: pd.DataFrame, expected: np.ndarray
) -> None:
    loaded_model = mlflow.lightgbm.load_model(model_uri)
    reload_prediction = clip_negative_predictions(
        loaded_model.predict(x_validation.head(10))
    )
    if not np.allclose(reload_prediction, expected[:10]):
        raise ValueError("Reloaded MLflow model predictions do not match")


def _git_commit() -> str:
    configured_commit = os.getenv("SOURCE_GIT_COMMIT")
    if configured_commit:
        return configured_commit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unavailable-in-runtime"
    return result.stdout.strip() if result.returncode == 0 else "uncommitted"


def _group_metrics(
    validation: pd.DataFrame, predicted: np.ndarray, column: str
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for value, indices in validation.groupby(column, observed=True).groups.items():
        positions = validation.index.get_indexer(indices)
        actual = validation.loc[indices, "target_quantity"].to_numpy(dtype="float64")
        result[str(value)] = metric_set(actual, predicted[positions])
    return result


def train_and_log(
    database_url: str,
    tracking_uri: str,
    feature_version: str,
    output_path: Path,
    validation_days: int = 28,
    random_seed: int = 2026,
    experiment_name: str = "sku-demand-training",
    min_wape_improvement: float = -0.05,
    registered_model_names: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    with psycopg.connect(database_url) as connection:
        frame = read_features(connection, feature_version)
    train, validation, validation_start = time_based_split(frame, validation_days)
    x_train, x_validation, y_train, y_validation = build_lgb_dataset(train, validation)
    model, predicted = fit_lightgbm(
        x_train, y_train, x_validation, y_validation, random_seed=random_seed
    )
    actual = y_validation.to_numpy(dtype="float64")
    baseline_metrics = {
        name: metric_set(actual, values)
        for name, values in baseline_predictions(validation).items()
    }
    model_metrics = metric_set(actual, predicted)
    best_baseline_wape = min(metrics["wape"] for metrics in baseline_metrics.values())
    passes_rule = model_metrics["wape"] <= best_baseline_wape * (
        1.0 - min_wape_improvement
    )
    model_names = registered_model_names or (MODEL_NAME,)

    validation_report = validation.reset_index(drop=True)
    sku_group = pd.qcut(
        validation_report["sku_avg_sales"].rank(method="first"),
        q=2,
        labels=["slow_moving", "fast_moving"],
    )
    validation_report["sku_group"] = sku_group
    report: dict[str, Any] = {
        "feature_version": feature_version,
        "random_seed": random_seed,
        "training_rows": len(train),
        "validation_rows": len(validation),
        "training_target_date_range": [
            str(train["target_date"].min()),
            str(train["target_date"].max()),
        ],
        "validation_target_date_range": [
            str(validation["target_date"].min()),
            str(validation["target_date"].max()),
        ],
        "validation_start_date": str(validation_start),
        "metrics": {"lightgbm": model_metrics, **baseline_metrics},
        "metrics_by_horizon": _group_metrics(validation_report, predicted, "horizon"),
        "metrics_by_sku_group": _group_metrics(
            validation_report, predicted, "sku_group"
        ),
        "promotion_rule": (
            "lightgbm_wape <= best_baseline_wape * "
            f"{1.0 - min_wape_improvement:.6g}"
        ),
        "min_wape_improvement": min_wape_improvement,
        "best_baseline_wape": best_baseline_wape,
        "passed_registration_rule": passes_rule,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)
    with mlflow.start_run(run_name=f"sprint-04-{feature_version}") as run:
        mlflow.log_params(
            {
                "feature_version": feature_version,
                "validation_days": validation_days,
                "random_seed": random_seed,
                "training_rows": len(train),
                "validation_rows": len(validation),
                "source_git_commit": _git_commit(),
            }
        )
        mlflow.log_metrics(
            {
                "lightgbm_wape": model_metrics["wape"],
                "lightgbm_mae": model_metrics["mae"],
                "lightgbm_rmse": model_metrics["rmse"],
                "lightgbm_smape": model_metrics["smape"],
                "best_baseline_wape": best_baseline_wape,
            }
        )
        mlflow.log_artifact(str(output_path), artifact_path="evaluation")
        project_root = Path(__file__).resolve().parents[2]
        source_files = [
            Path(__file__),
            Path(__file__).with_name("features.py"),
            project_root / "scripts/train_model.py",
            project_root / "feature_registry.yaml",
            project_root / "dags/factory.py",
            Path("/opt/airflow/dags/factory.py"),
        ]
        for source_file in source_files:
            if source_file.exists():
                mlflow.log_artifact(str(source_file), artifact_path="source")
        model_info = mlflow.lightgbm.log_model(
            model,
            artifact_path="model",
            signature=infer_signature(x_validation, predicted),
            input_example=x_validation.head(5),
            registered_model_name=model_names[0] if passes_rule else None,
        )
        run_id = run.info.run_id

    # MLflow 3 stores logged models under a models:/m-... URI instead of the
    # legacy runs:/<run_id>/<artifact_path> location.
    _verify_logged_model(model_info.model_uri, x_validation, predicted)

    registered_versions: dict[str, str] = {}
    if passes_rule:
        client = MlflowClient(tracking_uri=tracking_uri)
        versions = client.search_model_versions(f"run_id = '{run_id}'")
        matching = [version for version in versions if version.name == model_names[0]]
        if not matching:
            raise ValueError("Registered model version not found after MLflow logging")
        primary_version = str(matching[-1].version)
        registered_versions[model_names[0]] = primary_version
        client.transition_model_version_stage(
            name=model_names[0], version=primary_version, stage="Staging"
        )
        for alias_name in model_names[1:]:
            alias_version = mlflow.register_model(model_info.model_uri, alias_name)
            registered_versions[alias_name] = str(alias_version.version)
            client.transition_model_version_stage(
                name=alias_name,
                version=str(alias_version.version),
                stage="Staging",
            )

    report.update(
        {
            "mlflow_run_id": run_id,
            "model_uri": model_info.model_uri,
            "registered_model_name": model_names[0] if passes_rule else None,
            "registered_model_version": registered_versions.get(model_names[0]),
            "registered_models": registered_versions,
            "model_reloaded_and_predicted": True,
        }
    )
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def train_for_dataset(cfg: DatasetConfig) -> dict[str, Any]:
    """Thin per-dataset adapter over `train_and_log` (Sprint 4 entry point).

    `train_and_log` -> `read_features()` reads from a hardcoded Postgres
    table (`features.offline_sku_features`) keyed by `feature_version`;
    there is no table parameter to thread `cfg.table_name` into without
    restructuring `read_features`, which is out of scope here. What maps
    cleanly, per the brief, is the MLflow experiment name (`cfg.name`) and
    the validation window (`cfg.training.validation_days`) -- both are
    existing parameters of `train_and_log`. `feature_version` uses today's
    hbaac default (matching `scripts/train_model.py`) since this adapter
    has no batch_id to key it by; a real per-dataset feature_version handoff
    would need the pipeline runner to pass one through, which is out of
    scope for this thin adapter.
    """
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://forecast:forecast-local-only@localhost:5432/sku_forecasting",
    )
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    feature_version = os.environ.get(
        "FEATURE_VERSION", "sprint-03-v1-top100-a60-h56"
    )
    output_path = Path(f"data/features/evaluation_{cfg.name}.json")
    model_names = (f"{cfg.name}-forecaster",)
    if cfg.name == "hbaac_sku":
        model_names = (MODEL_NAME, *model_names)
    return train_and_log(
        database_url,
        tracking_uri,
        feature_version,
        output_path,
        validation_days=cfg.training.validation_days,
        experiment_name=cfg.name,
        min_wape_improvement=cfg.training.min_wape_improvement,
        registered_model_names=model_names,
    )
