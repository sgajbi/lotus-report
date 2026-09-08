-- report#350: report_request identity is the caller's idempotency key alone, so
-- two tenants choosing the same string collide in the job ledger that backs
-- every report job route.
--
-- Measured through the real HTTP materialization route before this change:
--   tenant-a -> 202
--   tenant-b -> 409 {"code": "idempotency_conflict",
--                    "message": "Idempotency-Key was reused with different idea evidence content."}
-- Tenant B reused nothing and sent identical content. It collided with another
-- tenant's request, and the refusal named a cause that was not true. Where the
-- bodies match, both tenants resolve to ONE stored report_request_id, which is
-- the more serious half.
--
-- `tenant_id` is already NOT NULL and populated on every row, so identity moves
-- from (idempotency_key) to (tenant_id, idempotency_key) with no backfill and
-- nothing to attribute. That is the whole reason this is safe to do in one
-- step, unlike migration 025 where the tenant had to be recovered from a JSON
-- blob and unattributable rows were refused rather than defaulted.
--
-- `report_request_id` is NOT re-derived. lotus-idea confirmed under C5-X03 that
-- owner_request_id, owner_realization_id and the rest of the materialization
-- receipt identity are immutable. Re-deriving them would hand a consumer an
-- identity it has never seen for a receipt it already holds.

-- Guarded, like the ADD below. `ALTER TABLE` takes ACCESS EXCLUSIVE on the
-- table whether or not `IF EXISTS` matches anything, so an unconditional drop
-- locked out every reader and writer at each startup to remove a constraint
-- that had not existed since the first run. Measured with a concurrent
-- connection reading pg_locks while the migration's transaction was open.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'report_request_idempotency_key_key'
          AND conrelid = 'report_request'::regclass
    ) THEN
        ALTER TABLE report_request
            DROP CONSTRAINT report_request_idempotency_key_key;
    END IF;
END
$$;

-- A named UNIQUE constraint rather than a bare index: identity is a contract,
-- and `scripts/migration_contract_check.py` asserts it through pg_constraint.
--
-- ADDED ONLY WHEN ABSENT. This was a DROP followed by an unconditional ADD,
-- which the runner re-executes on every startup: measured against a populated
-- table, the backing index was rebuilt each time -- a new relfilenode, an
-- ACCESS EXCLUSIVE lock and a full index build proportional to the row count,
-- at every deployment. Worse, between the DROP and the ADD the uniqueness
-- guarantee did not exist at all, so a concurrent writer could insert the
-- duplicate that then made the ADD fail and the startup abort.
--
-- The guard is a DO block, which this file previously could not use: the
-- runner split statements on every semicolon and would have torn the body
-- apart. That splitter is now quote- and dollar-quote aware, so the idiomatic
-- form is available and the migration no longer has to be shaped around it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'report_request_tenant_idempotency_key'
          AND conrelid = 'report_request'::regclass
    ) THEN
        ALTER TABLE report_request
            ADD CONSTRAINT report_request_tenant_idempotency_key
            UNIQUE (tenant_id, idempotency_key);
    END IF;
END
$$;
