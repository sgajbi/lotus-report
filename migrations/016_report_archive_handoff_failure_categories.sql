-- render#120 cutover: lotus-render is the one archive transmit authority and
-- Report records the custody outcome it reports. Three new failure categories:
--   archive_outcome_unknown       - the handoff deadline expired after the
--                                   delivery may have committed. the derived
--                                   areq_ request id is recorded for
--                                   reconciliation and a retry converges.
--   archive_handoff_failed        - Render reported the handoff failed.
--                                   terminal when Archive refused with a 4xx
--                                   (a deterministic re-render redeclares the
--                                   same digest and re-fails identically),
--                                   retry-eligible otherwise.
--   archive_handoff_not_configured- the render completed but no handoff
--                                   applied. a governed document is never
--                                   silently left out of custody.

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
        'archive_outcome_unknown',
        'archive_handoff_failed',
        'archive_handoff_not_configured',
        'timeout',
        'cancelled',
        'operator_intervention_required'
    )
);
