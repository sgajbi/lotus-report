-- Template selection becomes an immutable job fact (steering 2026-09-04):
-- PDF-capable jobs are stamped with their governed template id/version at
-- acceptance, and rendering fails closed without one. Every job accepted
-- before this change was accepted while all four families ordered their v1
-- template, so that historical posture is assigned deterministically here -
-- never decided from the clock during rendering, and never overwriting a
-- value already recorded at render time.
UPDATE report_job
SET render_template_id = CASE report_type
        WHEN 'portfolio_review' THEN 'portfolio-review'
        WHEN 'proof_pack' THEN 'proof-pack'
        WHEN 'outcome_review' THEN 'outcome-review'
        WHEN 'rebalance_wave' THEN 'rebalance-wave'
    END,
    render_template_version = 'v1'
WHERE render_template_version IS NULL
  AND report_type IN ('portfolio_review', 'proof_pack', 'outcome_review', 'rebalance_wave')
  AND report_request_id IN (
      SELECT report_request_id
      FROM report_request
      -- requested_output_formats_json is TEXT on SQLite and JSONB on
      -- PostgreSQL. The cast makes the containment test portable.
      WHERE CAST(requested_output_formats_json AS TEXT) LIKE '%"pdf"%'
  );
