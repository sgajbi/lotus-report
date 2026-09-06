-- report#326: bring the Idea evidence intake ledger under the same schema
-- management as the job and batch ledgers.
--
-- Before this migration the ledger was SQLite in the container filesystem.
-- report#329 gave it a durable volume, which fixed loss on container
-- replacement but left it with no migration standard and no migration gate:
-- docs/standards/migration-contract.md scopes itself to PostgreSQL and treats
-- SQLite as a unit-test adapter only.
--
-- idempotency_key is the replay identity. It is the primary key here for the
-- same reason it was in SQLite: a duplicate intake must collide rather than
-- insert, and that collision is the idempotency guarantee rather than a
-- constraint that happens to be convenient.
--
-- Timestamps are TIMESTAMPTZ, not TEXT. The SQLite ledger stored ISO strings
-- because SQLite has no timestamp type; carrying that choice into PostgreSQL
-- would preserve a limitation as if it were a decision, and would leave
-- ordering and retention comparisons doing string arithmetic on values whose
-- offset is not enforced.
CREATE TABLE IF NOT EXISTS idea_evidence_intake (
    idempotency_key TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    response_json JSONB NOT NULL,
    caller_context_json JSONB NOT NULL,
    report_evidence_pack_id TEXT NOT NULL,
    conversion_intent_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    evidence_packet_id TEXT NOT NULL,
    evidence_content_fingerprint TEXT NOT NULL,
    producer TEXT NOT NULL,
    supportability_status TEXT NOT NULL,
    accepted_at_utc TIMESTAMPTZ NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL,
    correlation_id TEXT,
    trace_id TEXT
);

-- Mirrors the SQLite indexes: source lookup for reconciliation, and created_at
-- for retention and operational listing.
CREATE INDEX IF NOT EXISTS idx_idea_evidence_intake_source
    ON idea_evidence_intake (report_evidence_pack_id, evidence_packet_id);

CREATE INDEX IF NOT EXISTS idx_idea_evidence_intake_created
    ON idea_evidence_intake (created_at_utc);
