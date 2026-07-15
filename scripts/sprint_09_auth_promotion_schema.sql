-- Sprint 09: auth users + model promotion approval workflow.

CREATE SCHEMA IF NOT EXISTS mlops;

CREATE TABLE IF NOT EXISTS mlops.users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('dev', 'manager')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mlops.promotion_requests (
    id BIGSERIAL PRIMARY KEY,
    dataset TEXT NOT NULL,
    model_name TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    current_prod_version TEXT,
    metrics_snapshot JSONB NOT NULL,
    requested_by TEXT NOT NULL,
    request_note TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by TEXT,
    review_comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_promotion_requests_status
    ON mlops.promotion_requests(status);
CREATE INDEX IF NOT EXISTS idx_promotion_requests_dataset
    ON mlops.promotion_requests(dataset, status);
