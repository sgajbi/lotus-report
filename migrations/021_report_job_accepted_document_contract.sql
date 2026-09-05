-- report#283 finding 6: EVERY contract axis a job is accepted under is
-- resolved once at acceptance and persisted here, so no lifecycle path
-- reinterprets an accepted job against today's definitions. NULL on jobs
-- accepted before the contract existed -- such jobs resolve current
-- definitions with no accepted-contract claim, never a fabricated one.
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS accepted_document_contract_json JSONB;
