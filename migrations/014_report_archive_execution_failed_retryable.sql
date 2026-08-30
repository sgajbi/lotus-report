-- archive_execution_failed becomes retry-eligible for portfolio-review
-- jobs (issue #211): their replay resolves the original
-- arch_{render_job_id} against archive before re-rendering, so a
-- committed-but-response-lost ingest is adopted rather than duplicated.
-- The category has a single producer (the archive-posture fallback), so a
-- category-scoped backfill converts exactly the stranded rows. Other report
-- families and rerender attempts keep the non-retryable posture: they have
-- no resolution path, and a fresh identity would defeat archive idempotency.

UPDATE report_job
SET retry_eligible = TRUE
WHERE status = 'failed'
  AND failure_category = 'archive_execution_failed'
  AND report_type = 'portfolio_review'
  AND retry_eligible = FALSE;
