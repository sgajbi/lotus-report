-- Canonical report-revision identity minted at capture (report#283).
-- The revision binding lives BESIDE the payload, never inside it, so the
-- id can never be part of its own preimage. snapshot_hash keeps covering
-- the complete stored bytes untouched. Columns stay NULL on failed
-- captures and on rows captured before revision identity existed --
-- history is never relabelled with identities it did not state.
ALTER TABLE report_input_snapshot ADD COLUMN IF NOT EXISTS report_revision_id TEXT;
ALTER TABLE report_input_snapshot ADD COLUMN IF NOT EXISTS series_digest TEXT;
ALTER TABLE report_input_snapshot ADD COLUMN IF NOT EXISTS source_revision_digest TEXT;
ALTER TABLE report_input_snapshot ADD COLUMN IF NOT EXISTS factual_content_digest TEXT;
ALTER TABLE report_input_snapshot ADD COLUMN IF NOT EXISTS factual_boundary_version TEXT;
ALTER TABLE report_input_snapshot ADD COLUMN IF NOT EXISTS source_revision_vector_json JSONB;

CREATE INDEX IF NOT EXISTS idx_report_input_snapshot_revision
ON report_input_snapshot(report_revision_id);
