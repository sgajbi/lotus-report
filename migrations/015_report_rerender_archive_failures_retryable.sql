-- Archive-stage rerender-attempt failures become retry-eligible (issue
-- #215): rerender recovery is now resolution-first - a new attempt is only
-- permitted after the failed attempt's own arch_{render_job_id} resolves to
-- a confirmed 404, and a committed correction is adopted instead. The two
-- categories have a single producer (the shared archive-posture helper), so
-- a category-scoped backfill converts exactly the stranded rows, and the
-- retry_eligible = FALSE guard makes re-applies no-ops.

UPDATE report_rerender_attempt
SET retry_eligible = TRUE
WHERE status = 'failed'
  AND failure_category IN ('archive_storage_failed', 'archive_execution_failed')
  AND retry_eligible = FALSE;
