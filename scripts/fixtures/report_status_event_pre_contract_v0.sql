CREATE TABLE report_request (
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

CREATE TABLE report_job (
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

CREATE TABLE report_status_event (
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
    message TEXT,
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    correlation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL
);

INSERT INTO report_request (
    report_request_id,
    report_type,
    portfolio_scope_json,
    requested_output_formats_json,
    as_of_date,
    reporting_currency,
    options_json,
    trigger_type,
    triggered_by,
    caller_application,
    tenant_id,
    region,
    idempotency_key,
    request_hash,
    correlation_id,
    trace_id,
    created_at
) VALUES (
    'request-pre-contract-v0',
    'portfolio_review',
    '{"portfolio_ids":["PB_SG_GLOBAL_BAL_001"]}'::jsonb,
    '["json"]'::jsonb,
    DATE '2025-12-31',
    'USD',
    '{}'::jsonb,
    'advisor_request',
    'advisor-fixture',
    'migration-upgrade-check',
    'tenant-fixture',
    'SG',
    'idempotency-pre-contract-v0',
    'hash-pre-contract-v0',
    'corr-pre-contract-v0',
    'trace-pre-contract-v0',
    TIMESTAMPTZ '2026-07-01T00:00:00Z'
);

INSERT INTO report_job (
    report_job_id,
    report_request_id,
    report_type,
    portfolio_scope_json,
    status,
    current_step,
    retry_eligible,
    cancel_requested,
    created_at,
    updated_at
) VALUES (
    'job-pre-contract-v0',
    'request-pre-contract-v0',
    'portfolio_review',
    '{"portfolio_ids":["PB_SG_GLOBAL_BAL_001"]}'::jsonb,
    'accepted',
    'request_accepted',
    TRUE,
    FALSE,
    TIMESTAMPTZ '2026-07-01T00:00:00Z',
    TIMESTAMPTZ '2026-07-01T00:00:00Z'
);

INSERT INTO report_status_event (
    status_event_id,
    report_job_id,
    from_status,
    to_status,
    event_type,
    message,
    actor,
    created_at,
    correlation_id,
    trace_id
) VALUES (
    'event-pre-contract-v0',
    'job-pre-contract-v0',
    NULL,
    'accepted',
    'job_created',
    'Legacy event retained for executable upgrade proof.',
    'migration-upgrade-check',
    TIMESTAMPTZ '2026-07-01T00:00:00Z',
    'corr-pre-contract-v0',
    'trace-pre-contract-v0'
);
