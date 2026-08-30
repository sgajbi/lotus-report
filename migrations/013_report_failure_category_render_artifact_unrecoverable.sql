-- Extend the failure-category vocabulary with render_artifact_unrecoverable:
-- a replayed "rendered" response that carries no artifact bytes (lotus-render
-- returns terminal truth without re-rendering and does not persist artifacts).
-- The posture is retry-eligible - RFC-0105 replay regenerates the document
-- deterministically from the retained snapshot under a fresh render job id.

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

ALTER TABLE report_rerender_attempt
DROP CONSTRAINT IF EXISTS report_rerender_attempt_failure_category_check;

ALTER TABLE report_rerender_attempt
ADD CONSTRAINT report_rerender_attempt_failure_category_check
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
