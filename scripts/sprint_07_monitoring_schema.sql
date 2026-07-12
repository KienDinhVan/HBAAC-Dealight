CREATE SCHEMA IF NOT EXISTS monitoring;

CREATE TABLE IF NOT EXISTS monitoring.forecast_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES serving.forecast_runs(run_id),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    status TEXT NOT NULL CHECK (status IN ('success', 'alert')),
    forecast_row_count BIGINT NOT NULL,
    sku_count BIGINT NOT NULL,
    horizon_count INTEGER NOT NULL,
    missing_sku_count BIGINT NOT NULL,
    negative_prediction_count BIGINT NOT NULL,
    prediction_min DOUBLE PRECISION,
    prediction_mean DOUBLE PRECISION,
    prediction_max DOUBLE PRECISION,
    zero_ratio DOUBLE PRECISION,
    actual_row_count BIGINT NOT NULL DEFAULT 0,
    accuracy_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    drift_detected BOOLEAN NOT NULL DEFAULT false,
    drift_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    alerts JSONB NOT NULL DEFAULT '[]'::jsonb,
    data_drift_report_path TEXT,
    prediction_drift_report_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_monitoring_forecast_reports_generated
    ON monitoring.forecast_reports(generated_at DESC);
