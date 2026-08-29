-- Durable, caller-defined recurring report-pack schedules (issue #167).
-- Definitions are tenant-fenced governance objects and every change is audited.

CREATE TABLE IF NOT EXISTS report_batch_schedule_definition (
    schedule_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    region TEXT NOT NULL,
    booking_center_code TEXT,
    owner_actor TEXT NOT NULL,
    enabled BOOLEAN NOT NULL,
    cadence TEXT NOT NULL CHECK (cadence IN ('monthly_end', 'quarter_end')),
    portfolio_ids_json JSONB NOT NULL,
    requested_output_formats_json JSONB NOT NULL,
    reporting_currency TEXT,
    options_json JSONB NOT NULL,
    max_batch_size INTEGER NOT NULL CHECK (max_batch_size > 0),
    fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ
);

-- Identical concurrent creates converge at the database: one enabled schedule per
-- logical definition and execution scope. Disabled rows are history, not identity.
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_batch_schedule_fingerprint_enabled
ON report_batch_schedule_definition (fingerprint)
WHERE enabled;

CREATE INDEX IF NOT EXISTS idx_report_batch_schedule_tenant
ON report_batch_schedule_definition (tenant_id, enabled);

CREATE TABLE IF NOT EXISTS report_batch_schedule_audit (
    audit_sequence BIGSERIAL NOT NULL,
    audit_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES report_batch_schedule_definition(schedule_id),
    action TEXT NOT NULL CHECK (action IN ('created', 'updated', 'enabled', 'disabled')),
    actor TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    changes_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_batch_schedule_audit_schedule
ON report_batch_schedule_audit (schedule_id, audit_sequence);
