CREATE TABLE IF NOT EXISTS report_upstream_call (
    upstream_call_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES report_input_snapshot(snapshot_id),
    service_name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_hash TEXT,
    response_ref TEXT,
    status_code INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    supportability_status TEXT NOT NULL CHECK (
        supportability_status IN ('complete', 'partial', 'unavailable', 'not_supported', 'redacted', 'error')
    ),
    completeness_status TEXT NOT NULL CHECK (
        completeness_status IN ('complete', 'partial', 'unavailable', 'not_supported', 'redacted', 'error')
    ),
    failure_category TEXT NOT NULL CHECK (
        failure_category IN ('none', 'partial_data', 'unsupported_input', 'upstream_unavailable', 'upstream_error', 'timeout', 'redacted')
    ),
    failure_message TEXT,
    correlation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_upstream_call_snapshot
ON report_upstream_call(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_report_upstream_call_service_endpoint
ON report_upstream_call(service_name, endpoint);

CREATE INDEX IF NOT EXISTS idx_report_upstream_call_supportability
ON report_upstream_call(supportability_status);

CREATE INDEX IF NOT EXISTS idx_report_upstream_call_created
ON report_upstream_call(created_at);
