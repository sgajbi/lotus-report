-- report#283 trust separation: the evaluated source-cut coherence verdict
-- (status, policy_version, detail) is its own independently defensible
-- claim, persisted beside the snapshot. Policy-derived, so it never
-- participates in the revision preimage. NULL on failed captures and on
-- snapshots captured before the policy existed.
ALTER TABLE report_input_snapshot ADD COLUMN IF NOT EXISTS source_cut_coherence_json JSONB;
