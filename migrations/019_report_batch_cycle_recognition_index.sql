-- Cycle recognition runs once per enabled configured schedule on every
-- scheduler pass. The index keeps that lookup bounded as the append-only
-- batch ledger grows. Idempotent because the schema runner reapplies every
-- migration at startup.
CREATE INDEX IF NOT EXISTS idx_report_batch_cycle_recognition
ON report_batch (as_of_date, tenant_id, region);
