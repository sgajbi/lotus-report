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

-- Backfill: jobs and rerender attempts that already failed on the artifactless
-- replay were stored as archive_validation_failed / retry_eligible = false and
-- would stay unreplayable after upgrade. The old failure message below was
-- written only by that code path, so the match is exact.

UPDATE report_job
SET failure_category = 'render_artifact_unrecoverable',
    retry_eligible = TRUE,
    failure_message = 'The render completed previously but its artifact was only available in the original response. Replay the job to re-render from the retained snapshot.'
WHERE status = 'failed'
  AND failure_category = 'archive_validation_failed'
  AND failure_message = 'Rendered artifact payload was not available for archive handoff.';

UPDATE report_rerender_attempt
SET failure_category = 'render_artifact_unrecoverable',
    retry_eligible = TRUE,
    failure_message = 'The render completed previously but its artifact was only available in the original response. Request a new rerender attempt to regenerate it from the retained snapshot.'
WHERE failure_category = 'archive_validation_failed'
  AND failure_message = 'Rendered artifact payload was not available for archive handoff.';
