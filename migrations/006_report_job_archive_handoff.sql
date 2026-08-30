ALTER TABLE report_job
DROP CONSTRAINT IF EXISTS report_job_status_check;

ALTER TABLE report_job
ADD CONSTRAINT report_job_status_check
CHECK (
    status IN (
        'accepted',
        'queued',
        'collecting_data',
        'data_ready',
        'rendering',
        'completed',
        'archiving',
        'archived',
        'completed_with_warnings',
        'failed',
        'cancelled'
    )
);

ALTER TABLE report_job
DROP CONSTRAINT IF EXISTS report_job_failure_category_check;

ALTER TABLE report_job
ADD CONSTRAINT report_job_failure_category_check
CHECK (
    failure_category IS NULL
    OR failure_category IN (
        'entitlement_failed',
        'validation_failed',
        'upstream_data_failed',
        'data_incomplete',
        'render_validation_failed',
        'render_conflict',
        'render_execution_failed',
        'render_artifact_unrecoverable',
        'archive_validation_failed',
        'archive_conflict',
        'archive_storage_failed',
        'archive_execution_failed',
        'timeout',
        'cancelled',
        'operator_intervention_required'
    )
);

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS archive_request_id TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS archive_document_id TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS archive_completed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_report_job_archive_document
ON report_job(archive_document_id)
WHERE archive_document_id IS NOT NULL;

ALTER TABLE report_status_event
DROP CONSTRAINT IF EXISTS report_status_event_to_status_check;

ALTER TABLE report_status_event
ADD CONSTRAINT report_status_event_to_status_check
CHECK (
    to_status IN (
        'accepted',
        'queued',
        'collecting_data',
        'data_ready',
        'rendering',
        'completed',
        'archiving',
        'archived',
        'completed_with_warnings',
        'failed',
        'cancelled'
    )
);
