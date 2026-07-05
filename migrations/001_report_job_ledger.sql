CREATE TABLE IF NOT EXISTS report_request (
    report_request_id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    portfolio_scope_json JSONB NOT NULL,
    requested_output_formats_json JSONB NOT NULL,
    as_of_date DATE NOT NULL,
    reporting_currency TEXT,
    options_json JSONB NOT NULL,
    trigger_type TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    caller_application TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    region TEXT NOT NULL,
    booking_center_code TEXT,
    role TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS report_job (
    report_job_id TEXT PRIMARY KEY,
    report_request_id TEXT NOT NULL REFERENCES report_request(report_request_id),
    report_type TEXT NOT NULL,
    portfolio_scope_json JSONB NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'accepted',
            'queued',
            'collecting_data',
            'data_ready',
            'completed',
            'completed_with_warnings',
            'failed',
            'cancelled'
        )
    ),
    failure_category TEXT CHECK (
        failure_category IS NULL
        OR failure_category IN (
            'entitlement_failed',
            'validation_failed',
            'upstream_data_failed',
            'data_incomplete',
            'render_validation_failed',
            'render_conflict',
            'render_execution_failed',
            'archive_validation_failed',
            'archive_conflict',
            'archive_storage_failed',
            'archive_execution_failed',
            'timeout',
            'cancelled',
            'operator_intervention_required'
        )
    ),
    failure_message TEXT,
    current_step TEXT NOT NULL,
    retry_eligible BOOLEAN NOT NULL,
    cancel_requested BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS report_status_event (
    status_event_id TEXT PRIMARY KEY,
    report_job_id TEXT NOT NULL REFERENCES report_job(report_job_id),
    from_status TEXT,
    to_status TEXT NOT NULL CHECK (
        to_status IN (
            'accepted',
            'queued',
            'collecting_data',
            'data_ready',
            'completed',
            'completed_with_warnings',
            'failed',
            'cancelled'
        )
    ),
    event_type TEXT NOT NULL,
    event_schema_version TEXT NOT NULL DEFAULT 'report-status-event.v1',
    event_family TEXT NOT NULL DEFAULT 'job_lifecycle',
    event_payload_json JSONB NOT NULL DEFAULT '{}',
    event_idempotency_key TEXT,
    message TEXT,
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    correlation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_request_created
ON report_request(created_at);

CREATE INDEX IF NOT EXISTS idx_report_request_tenant_region_created
ON report_request(tenant_id, region, created_at);

CREATE INDEX IF NOT EXISTS idx_report_request_as_of_date
ON report_request(as_of_date);

CREATE INDEX IF NOT EXISTS idx_report_request_scope_created
ON report_request USING GIN (portfolio_scope_json);

CREATE INDEX IF NOT EXISTS idx_report_job_status_updated
ON report_job(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_report_job_created
ON report_job(created_at);

CREATE INDEX IF NOT EXISTS idx_report_job_completed
ON report_job(completed_at)
WHERE completed_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_report_job_request
ON report_job(report_request_id);

CREATE INDEX IF NOT EXISTS idx_report_status_event_job_created
ON report_status_event(report_job_id, created_at);

CREATE INDEX IF NOT EXISTS idx_report_status_event_family_created
ON report_status_event(event_family, created_at);

CREATE INDEX IF NOT EXISTS idx_report_status_event_idempotency_key
ON report_status_event(event_idempotency_key)
WHERE event_idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS report_job_relationship (
    relationship_id TEXT PRIMARY KEY,
    source_report_job_id TEXT NOT NULL REFERENCES report_job(report_job_id),
    derived_report_job_id TEXT NOT NULL REFERENCES report_job(report_job_id),
    relationship_type TEXT NOT NULL CHECK (
        relationship_type IN (
            'regenerate_replacement',
            'failed_work_replay',
            'batch_item_replay'
        )
    ),
    source_status TEXT NOT NULL,
    derived_status TEXT NOT NULL,
    source_failure_category TEXT,
    derived_failure_category TEXT,
    archive_consequence TEXT,
    previous_archive_document_id TEXT,
    new_archive_document_id TEXT,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(source_report_job_id, derived_report_job_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_report_job_relationship_source_created
ON report_job_relationship(source_report_job_id, created_at);

CREATE INDEX IF NOT EXISTS idx_report_job_relationship_derived_created
ON report_job_relationship(derived_report_job_id, created_at);
