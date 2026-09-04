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
        'archive_outcome_unknown',
        'archive_handoff_failed',
        'archive_handoff_not_configured',
        'timeout',
        'cancelled',
        'operator_intervention_required'
    )
);

CREATE INDEX IF NOT EXISTS idx_report_request_tenant_region_created
ON report_request(tenant_id, region, created_at);

CREATE INDEX IF NOT EXISTS idx_report_request_as_of_date
ON report_request(as_of_date);

CREATE INDEX IF NOT EXISTS idx_report_job_created
ON report_job(created_at);

CREATE INDEX IF NOT EXISTS idx_report_job_completed
ON report_job(completed_at)
WHERE completed_at IS NOT NULL;
