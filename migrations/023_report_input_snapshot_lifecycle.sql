-- report#283 finding 4: the snapshot's stated lifecycle metadata --
-- governing policy reference/version, reproduction availability, and
-- the responsible lifecycle authority interface. Stated, never
-- enforced -- no retention automation exists here, and lotus-archive
-- remains the document lifecycle authority. NULL on rows captured
-- before the policy contract existed.
ALTER TABLE report_input_snapshot ADD COLUMN IF NOT EXISTS lifecycle_json JSONB;
