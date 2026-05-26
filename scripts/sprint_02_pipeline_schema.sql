CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS raw.transactions (
    batch_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_row_number BIGINT NOT NULL,
    date_raw TEXT,
    stt_raw TEXT,
    item_code_raw TEXT,
    quantity_raw TEXT,
    unit_price_raw TEXT,
    sales_amount_raw TEXT,
    unit_cost_raw TEXT,
    cost_amount_raw TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (batch_id, source_row_number)
);

CREATE TABLE IF NOT EXISTS bronze.transactions (
    batch_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_row_number BIGINT NOT NULL,
    stt BIGINT,
    date_raw TEXT,
    item_code_raw TEXT,
    quantity NUMERIC,
    unit_price_raw TEXT,
    sales_amount NUMERIC,
    unit_cost_raw TEXT,
    cost_amount NUMERIC,
    ingested_at TIMESTAMPTZ NOT NULL,
    transformed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (batch_id, source_row_number)
);

CREATE TABLE IF NOT EXISTS silver.transactions_clean (
    batch_id TEXT NOT NULL,
    source_row_number BIGINT NOT NULL,
    date DATE,
    item_code TEXT,
    quantity NUMERIC,
    sales_quantity NUMERIC,
    return_quantity NUMERIC,
    unit_price NUMERIC,
    sales_amount NUMERIC,
    unit_cost NUMERIC,
    cost_amount NUMERIC,
    is_return BOOLEAN NOT NULL,
    is_valid BOOLEAN NOT NULL,
    error_reason TEXT,
    transformed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (batch_id, source_row_number)
);

CREATE TABLE IF NOT EXISTS gold.daily_sku_sales (
    date DATE NOT NULL,
    item_code TEXT NOT NULL,
    quantity_sold NUMERIC NOT NULL,
    return_quantity NUMERIC NOT NULL,
    net_quantity NUMERIC NOT NULL,
    sales_amount NUMERIC,
    cost_amount NUMERIC,
    avg_unit_price NUMERIC,
    avg_unit_cost NUMERIC,
    transaction_count BIGINT NOT NULL CHECK (transaction_count >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (date, item_code)
);

CREATE INDEX IF NOT EXISTS idx_raw_transactions_batch
ON raw.transactions(batch_id);

CREATE INDEX IF NOT EXISTS idx_bronze_transactions_batch
ON bronze.transactions(batch_id);

CREATE INDEX IF NOT EXISTS idx_silver_transactions_batch_valid
ON silver.transactions_clean(batch_id, is_valid);
