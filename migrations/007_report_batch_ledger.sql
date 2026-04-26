CREATE TABLE IF NOT EXISTS report_batch (
    batch_id TEXT PRIMARY KEY,
    selector_mode TEXT NOT NULL CHECK (
        selector_mode IN (
            'explicit_portfolio_list',
            'selected_subset',
            'all_active_portfolios',
            'batch_manifest'
        )
    ),
    tenant_id TEXT NOT NULL,
    region TEXT NOT NULL,
    materialized_portfolio_ids_json JSONB NOT NULL,
    requested_output_formats_json JSONB NOT NULL,
    as_of_date DATE NOT NULL,
    reporting_currency TEXT,
    options_json JSONB NOT NULL,
    trigger_type TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    caller_application TEXT NOT NULL,
    booking_center_code TEXT,
    role TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('materialized', 'running')),
    item_count INTEGER NOT NULL CHECK (item_count > 0),
    correlation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS report_batch_item (
    batch_item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES report_batch(batch_id),
    item_position INTEGER NOT NULL CHECK (item_position > 0),
    portfolio_id TEXT NOT NULL,
    item_idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('materialized', 'leased', 'waiting_on_report_job')),
    source_system TEXT NOT NULL,
    source_object TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    report_job_id TEXT,
    lease_owner TEXT,
    lease_token TEXT,
    lease_acquired_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    dispatched_at TIMESTAMPTZ,
    UNIQUE(batch_id, portfolio_id),
    UNIQUE(batch_id, item_position)
);

ALTER TABLE report_batch
DROP CONSTRAINT IF EXISTS report_batch_status_check;

ALTER TABLE report_batch
ADD CONSTRAINT report_batch_status_check
CHECK (status IN ('materialized', 'running'));

ALTER TABLE report_batch_item
DROP CONSTRAINT IF EXISTS report_batch_item_status_check;

ALTER TABLE report_batch_item
ADD CONSTRAINT report_batch_item_status_check
CHECK (status IN ('materialized', 'leased', 'waiting_on_report_job'));

ALTER TABLE report_batch_item
ADD COLUMN IF NOT EXISTS report_job_id TEXT;

ALTER TABLE report_batch_item
ADD COLUMN IF NOT EXISTS lease_owner TEXT;

ALTER TABLE report_batch_item
ADD COLUMN IF NOT EXISTS lease_token TEXT;

ALTER TABLE report_batch_item
ADD COLUMN IF NOT EXISTS lease_acquired_at TIMESTAMPTZ;

ALTER TABLE report_batch_item
ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

ALTER TABLE report_batch_item
ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;

ALTER TABLE report_batch_item
ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_report_batch_created
ON report_batch(created_at);

CREATE INDEX IF NOT EXISTS idx_report_batch_tenant_region_created
ON report_batch(tenant_id, region, created_at);

CREATE INDEX IF NOT EXISTS idx_report_batch_status_created
ON report_batch(status, created_at);

CREATE INDEX IF NOT EXISTS idx_report_batch_item_batch_position
ON report_batch_item(batch_id, item_position);

CREATE INDEX IF NOT EXISTS idx_report_batch_item_portfolio
ON report_batch_item(portfolio_id);

CREATE INDEX IF NOT EXISTS idx_report_batch_item_status_created
ON report_batch_item(status, created_at);

CREATE INDEX IF NOT EXISTS idx_report_batch_item_lease_expiry
ON report_batch_item(status, lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_report_batch_item_report_job
ON report_batch_item(report_job_id);
