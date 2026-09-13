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
        -- A retained volume migrated by this file's ORIGINAL shape holds the
        -- identity as a bare UNIQUE INDEX of this name with no pg_constraint
        -- row (observed in the catalog of a real pre-change volume during
        -- #376). The name is occupied, so a plain ADD CONSTRAINT dies with
        -- 42P07 and startup aborts. Promote the existing index instead:
        -- catalog-only, no rebuild, same relfilenode, and the pg_constraint
        -- contract that migration_contract_check asserts is finally satisfied
        -- on that path. Editing this shipped file is legitimate for exactly
        -- this repair: every volume in the affected state predates the
        -- applied-migration ledger, so this file still re-runs for all of
        -- them, and a volume that already recorded it holds the constraint
        -- form this branch would no-op on anyway.
        IF EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_index i ON i.indexrelid = c.oid
            WHERE c.relname = 'report_request_tenant_idempotency_key'
              AND i.indrelid = 'report_request'::regclass
        ) THEN
            -- Promote ONLY the exact identity definition. Binding the name
            -- alone would bless a same-name index with different uniqueness
            -- as the tenant identity, which is an invented constraint and
            -- worse than an abort. Anything else refuses without promoting
            -- or dropping. Exact column ORDER also keeps the promoted
            -- definition fingerprint-identical to a fresh install.
            IF EXISTS (
                SELECT 1 FROM pg_class c
                JOIN pg_index i ON i.indexrelid = c.oid
                WHERE c.relname = 'report_request_tenant_idempotency_key'
                  AND i.indrelid = 'report_request'::regclass
                  AND i.indisunique
                  AND i.indnatts = 2
                  AND i.indnkeyatts = 2
                  AND i.indpred IS NULL
                  AND i.indexprs IS NULL
                  AND (
                      SELECT array_agg(a.attname::text ORDER BY k.ord)
                      FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a
                        ON a.attrelid = i.indrelid AND a.attnum = k.attnum
                  ) = ARRAY['tenant_id', 'idempotency_key']
            ) THEN
                ALTER TABLE report_request
                    ADD CONSTRAINT report_request_tenant_idempotency_key
                    UNIQUE USING INDEX report_request_tenant_idempotency_key;
            ELSE
                RAISE EXCEPTION 'report_request_identity_index_unexpected_definition: a relation named report_request_tenant_idempotency_key exists but is not the exact UNIQUE (tenant_id, idempotency_key) index. Refusing to promote or drop it';
            END IF;
        ELSE
            ALTER TABLE report_request
                ADD CONSTRAINT report_request_tenant_idempotency_key
                UNIQUE (tenant_id, idempotency_key);
        END IF;
    END IF;
END
$$;
