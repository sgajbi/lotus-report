-- Render states template_publication (published or development) at render
-- time. Report persists that statement verbatim beside the template version
-- and custody facts, so the job projection can answer: what version was
-- ordered, what publication posture did it render under, and is the exact
-- artifact in Archive. Custody (archived) and publication remain DISTINCT
-- facts. External distribution authority is Gateway/Archive-owned - Report
-- records evidence and never gates distribution itself. Null on jobs
-- completed before Render stated the posture.
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS render_template_publication TEXT;
