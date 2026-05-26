CREATE SCHEMA IF NOT EXISTS serving;

CREATE TABLE IF NOT EXISTS serving.forecast_runs (
    run_id TEXT PRIMARY KEY,
    forecast_date DATE NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    row_count BIGINT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS serving.sku_forecast (
    run_id TEXT NOT NULL REFERENCES serving.forecast_runs(run_id) ON DELETE CASCADE,
    forecast_date DATE NOT NULL,
    item_code TEXT NOT NULL,
    target_date DATE NOT NULL,
    horizon INT NOT NULL CHECK (horizon BETWEEN 1 AND 56),
    predicted_quantity DOUBLE PRECISION NOT NULL CHECK (predicted_quantity >= 0),
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prediction_source TEXT NOT NULL DEFAULT 'lightgbm',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id, item_code, target_date)
);

ALTER TABLE serving.sku_forecast
    ADD COLUMN IF NOT EXISTS prediction_source TEXT NOT NULL DEFAULT 'lightgbm';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'sku_forecast_prediction_source_check'
    ) THEN
        ALTER TABLE serving.sku_forecast
            ADD CONSTRAINT sku_forecast_prediction_source_check
            CHECK (prediction_source IN ('lightgbm', 'seasonal_naive_lag_7'));
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_sku_forecast_item_date
    ON serving.sku_forecast(item_code, forecast_date, target_date);

CREATE INDEX IF NOT EXISTS idx_sku_forecast_run
    ON serving.sku_forecast(run_id);

CREATE INDEX IF NOT EXISTS idx_sku_forecast_target_quantity
    ON serving.sku_forecast(run_id, target_date, predicted_quantity DESC);
