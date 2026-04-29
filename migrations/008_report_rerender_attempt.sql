CREATE TABLE IF NOT EXISTS report_rerender_attempt (
    rerender_attempt_id TEXT PRIMARY KEY,
    report_job_id TEXT NOT NULL REFERENCES report_job(report_job_id),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('rendering', 'rendered', 'archiving', 'archived', 'failed')
    ),
    snapshot_id TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    previous_render_job_id TEXT,
    previous_archive_document_id TEXT,
    render_job_id TEXT NOT NULL,
    render_output_format TEXT NOT NULL,
    render_template_id TEXT NOT NULL,
    render_template_version TEXT NOT NULL,
    render_artifact_sha256 TEXT,
    render_bounded_determinism_fingerprint TEXT,
    render_runtime_engine TEXT,
    render_runtime_engine_version TEXT,
    render_duration_ms INTEGER,
    archive_request_id TEXT,
    archive_document_id TEXT,
    archive_completed_at TIMESTAMPTZ,
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
    retry_eligible BOOLEAN NOT NULL,
    requested_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(report_job_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_report_rerender_attempt_job_created
ON report_rerender_attempt(report_job_id, created_at);

CREATE INDEX IF NOT EXISTS idx_report_rerender_attempt_archive_document
ON report_rerender_attempt(archive_document_id)
WHERE archive_document_id IS NOT NULL;
