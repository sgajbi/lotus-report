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
        'completed_with_warnings',
        'archiving',
        'archived',
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
ADD COLUMN IF NOT EXISTS render_job_id TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS render_output_format TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS render_template_id TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS render_template_version TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS render_artifact_sha256 TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS render_bounded_determinism_fingerprint TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS render_runtime_engine TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS render_runtime_engine_version TEXT;

ALTER TABLE report_job
ADD COLUMN IF NOT EXISTS render_duration_ms INTEGER;

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
        'completed_with_warnings',
        'archiving',
        'archived',
        'failed',
        'cancelled'
    )
);
