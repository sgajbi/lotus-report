CREATE TABLE IF NOT EXISTS report_input_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    report_job_id TEXT NOT NULL UNIQUE REFERENCES report_job(report_job_id),
    report_type TEXT NOT NULL,
    report_data_contract_version TEXT NOT NULL,
    portfolio_scope_json JSONB NOT NULL,
    as_of_date DATE NOT NULL,
    snapshot_payload_json JSONB NOT NULL,
    snapshot_hash TEXT NOT NULL,
    snapshot_storage_ref TEXT,
    supportability_status TEXT NOT NULL CHECK (
        supportability_status IN (
            'complete',
            'partial',
            'unavailable',
            'not_supported',
            'redacted',
            'error'
        )
    ),
    completeness_status TEXT NOT NULL CHECK (
        completeness_status IN (
            'complete',
            'partial',
            'unavailable',
            'not_supported',
            'redacted',
            'error'
        )
    ),
    lineage_summary_json JSONB NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    correlation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_input_snapshot_created
ON report_input_snapshot(created_at);

CREATE INDEX IF NOT EXISTS idx_report_input_snapshot_supportability
ON report_input_snapshot(supportability_status);

CREATE INDEX IF NOT EXISTS idx_report_input_snapshot_report_type_created
ON report_input_snapshot(report_type, created_at);
