from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg

from api.app.config import Settings, get_settings

VALUE_COLUMNS = [f"F{i}" for i in range(1, 29)]
EXPECTED_HORIZON = 56


def submission_to_forecast_frame(path: Path, forecast_date: date) -> pd.DataFrame:
    submission = pd.read_csv(path)
    expected_columns = ["id", *VALUE_COLUMNS]
    if submission.columns.tolist() != expected_columns:
        raise ValueError("Submission columns must be id followed by F1..F28")
    if submission[VALUE_COLUMNS].isna().any().any():
        raise ValueError("Submission contains missing predictions")
    if (submission[VALUE_COLUMNS] < 0).any().any():
        raise ValueError("Submission contains negative predictions")

    parts: list[pd.DataFrame] = []
    for suffix, offset in (("_validation", 0), ("_evaluation", 28)):
        selected = submission.loc[submission["id"].str.endswith(suffix)].copy()
        selected["item_code"] = selected["id"].str.removesuffix(suffix)
        melted = selected.melt(
            id_vars=["item_code"],
            value_vars=VALUE_COLUMNS,
            var_name="period",
            value_name="predicted_quantity",
        )
        melted["horizon"] = melted["period"].str[1:].astype("int32") + offset
        parts.append(melted[["item_code", "horizon", "predicted_quantity"]])

    forecasts = pd.concat(parts, ignore_index=True)
    sku_counts = forecasts.groupby("item_code")["horizon"].nunique()
    if len(sku_counts) == 0 or (sku_counts != EXPECTED_HORIZON).any():
        raise ValueError("Each SKU must have exactly 56 forecast horizons")
    if forecasts.duplicated(["item_code", "horizon"]).any():
        raise ValueError("Submission contains duplicate SKU/horizon records")

    forecasts["forecast_date"] = pd.Timestamp(forecast_date).date()
    forecasts["target_date"] = (
        pd.Timestamp(forecast_date) + pd.to_timedelta(forecasts["horizon"], unit="D")
    ).dt.date
    forecasts["predicted_quantity"] = forecasts["predicted_quantity"].astype("float64")
    return forecasts[
        ["forecast_date", "item_code", "target_date", "horizon", "predicted_quantity"]
    ].sort_values(["item_code", "horizon"])


def apply_schema(database_url: str, schema_file: Path) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(schema_file.read_text(encoding="utf-8"))


def load_submission(
    settings: Settings,
    schema_file: Path,
) -> int:
    source_path = Path(settings.production_submission_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Production submission does not exist: {source_path}")
    forecast_date = date.fromisoformat(settings.forecast_date)
    frame = submission_to_forecast_frame(source_path, forecast_date)

    apply_schema(settings.database_url, schema_file)
    try:
        with psycopg.connect(settings.database_url) as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO serving.forecast_runs (
                        run_id, forecast_date, model_name, model_version, status,
                        row_count, started_at, finished_at, error_message
                    )
                    VALUES (%s, %s, %s, %s, 'running', NULL, clock_timestamp(), NULL, NULL)
                    ON CONFLICT (run_id) DO UPDATE SET
                        forecast_date = EXCLUDED.forecast_date,
                        model_name = EXCLUDED.model_name,
                        model_version = EXCLUDED.model_version,
                        status = 'running',
                        row_count = NULL,
                        started_at = clock_timestamp(),
                        finished_at = NULL,
                        error_message = NULL
                    """,
                    (
                        settings.forecast_run_id,
                        forecast_date,
                        settings.model_name,
                        settings.model_version,
                    ),
                )
                connection.execute(
                    "DELETE FROM serving.sku_forecast WHERE run_id = %s",
                    (settings.forecast_run_id,),
                )
                with connection.cursor().copy(
                    """
                    COPY serving.sku_forecast (
                        run_id, forecast_date, item_code, target_date, horizon,
                        predicted_quantity, model_name, model_version
                    ) FROM STDIN
                    """
                ) as copy:
                    for record in frame.itertuples(index=False):
                        copy.write_row(
                            (
                                settings.forecast_run_id,
                                record.forecast_date,
                                record.item_code,
                                record.target_date,
                                int(record.horizon),
                                float(record.predicted_quantity),
                                settings.model_name,
                                settings.model_version,
                            )
                        )
                connection.execute(
                    """
                    UPDATE serving.forecast_runs
                    SET status = 'success', row_count = %s, finished_at = clock_timestamp()
                    WHERE run_id = %s
                    """,
                    (len(frame), settings.forecast_run_id),
                )
    except Exception as exc:
        with psycopg.connect(settings.database_url) as connection:
            connection.execute(
                """
                INSERT INTO serving.forecast_runs (
                    run_id, forecast_date, model_name, model_version, status,
                    started_at, finished_at, error_message
                )
                VALUES (%s, %s, %s, %s, 'failed', clock_timestamp(), clock_timestamp(), %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status = 'failed', finished_at = clock_timestamp(), error_message = EXCLUDED.error_message
                """,
                (
                    settings.forecast_run_id,
                    forecast_date,
                    settings.model_name,
                    settings.model_version,
                    str(exc)[:2000],
                ),
            )
        raise
    return len(frame)


def main() -> None:
    defaults = get_settings()
    parser = argparse.ArgumentParser(
        description="Load an approved submission into serving tables."
    )
    parser.add_argument(
        "--submission-path", default=defaults.production_submission_path
    )
    parser.add_argument("--forecast-date", default=defaults.forecast_date)
    parser.add_argument("--run-id", default=defaults.forecast_run_id)
    parser.add_argument("--model-name", default=defaults.model_name)
    parser.add_argument("--model-version", default=defaults.model_version)
    parser.add_argument("--schema-file", type=Path, default=Path("scripts/init_db.sql"))
    args = parser.parse_args()
    settings = Settings(
        service_name=defaults.service_name,
        service_version=defaults.service_version,
        database_url=defaults.database_url,
        production_submission_path=args.submission_path,
        forecast_date=args.forecast_date,
        forecast_run_id=args.run_id,
        model_name=args.model_name,
        model_version=args.model_version,
    )
    rows = load_submission(settings, args.schema_file)
    print(
        f"Loaded run_id={settings.forecast_run_id}, "
        f"rows={rows:,}, source={settings.production_submission_path}"
    )


if __name__ == "__main__":
    main()
