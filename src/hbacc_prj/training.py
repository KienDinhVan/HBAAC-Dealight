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
    passes_rule = model_metrics["wape"] <= best_baseline_wape * 1.05

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
        "promotion_rule": "lightgbm_wape <= best_baseline_wape * 1.05",
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
            project_root / "dags/dag_03_train_model.py",
            Path("/opt/airflow/dags/dag_03_train_model.py"),
        ]
        for source_file in source_files:
            if source_file.exists():
                mlflow.log_artifact(str(source_file), artifact_path="source")
        model_info = mlflow.lightgbm.log_model(
            model,
            artifact_path="model",
            signature=infer_signature(x_validation, predicted),
            input_example=x_validation.head(5),
            registered_model_name=MODEL_NAME if passes_rule else None,
        )
        run_id = run.info.run_id

    loaded_model = mlflow.lightgbm.load_model(f"runs:/{run_id}/model")
    reload_prediction = clip_negative_predictions(
        loaded_model.predict(x_validation.head(10))
    )
    if not np.allclose(reload_prediction, predicted[:10]):
        raise ValueError("Reloaded MLflow model predictions do not match")

    model_version: str | None = None
    if passes_rule:
        client = MlflowClient(tracking_uri=tracking_uri)
        versions = client.search_model_versions(f"run_id = '{run_id}'")
        matching = [version for version in versions if version.name == MODEL_NAME]
        if not matching:
            raise ValueError("Registered model version not found after MLflow logging")
        model_version = matching[-1].version
        client.transition_model_version_stage(
            name=MODEL_NAME, version=model_version, stage="Staging"
        )

    report.update(
        {
            "mlflow_run_id": run_id,
            "model_uri": model_info.model_uri,
            "registered_model_name": MODEL_NAME if passes_rule else None,
            "registered_model_version": model_version,
            "model_reloaded_and_predicted": True,
        }
    )
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report
