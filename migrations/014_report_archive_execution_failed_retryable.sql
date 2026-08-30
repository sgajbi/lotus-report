-- archive_execution_failed becomes retry-eligible (issue #211): archive
-- ingestion is idempotent by the deterministic arch_{render_job_id} request
-- id, so retrying an unclassified archive fault is convergent-safe by
-- construction. The category has a single producer (the archive-posture
-- fallback branch), so a category-scoped backfill converts exactly the rows
-- stranded under the old posture.

UPDATE report_job
SET retry_eligible = TRUE
WHERE status = 'failed'
  AND failure_category = 'archive_execution_failed'
  AND retry_eligible = FALSE;

UPDATE report_rerender_attempt
SET retry_eligible = TRUE
WHERE failure_category = 'archive_execution_failed'
  AND retry_eligible = FALSE;
