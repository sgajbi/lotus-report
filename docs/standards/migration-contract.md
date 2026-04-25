# Migration Contract Standard

- Service: `lotus-report`
- Persistence mode: **PostgreSQL report job ledger schema** for durable reporting request, job, and status
  lifecycle state.
- Migration policy: **forward-only schema management** with deterministic smoke validation.

## Deterministic Checks

- `make migration-smoke` validates that this contract document exists, applies the versioned
  PostgreSQL report job ledger schema, checks mandatory tables `report_request`, `report_job`,
  `report_status_event`, `report_input_snapshot`, and `report_upstream_call`, verifies required
  operational indexes, and verifies database-level idempotency uniqueness on
  `report_request.idempotency_key` plus the single-snapshot-per-job uniqueness posture on
  `report_input_snapshot.report_job_id`. It also verifies the archive handoff fields
  `archive_request_id`, `archive_document_id`, and `archive_completed_at`, the archive document
  lookup index, and the archive-aware status/failure-category constraints used by PDF report jobs.
- CI executes `make migration-smoke` on each PR against a dedicated PostgreSQL service container.
- Local migration smoke requires `REPORT_JOB_LEDGER_DATABASE_URL` and must not fall back to a file
  database. SQLite is retained only as an isolated unit-test adapter for fast ledger behavior tests.

## Rollback and Forward-Fix

- Runtime rollback is not implemented for the first durable ledger wave.
- Any contract issue is resolved through **forward-fix** in code/docs and re-run of CI gates.
- Destructive schema changes require a later RFC or ADR with explicit migration and archive impact.

## Operational Indexing

The first-wave ledger must keep these query paths indexed:

1. idempotent request lookup by `report_request.idempotency_key`,
2. support diagnostics by request creation time,
3. tenant/region/time filtering for operational support,
4. as-of-date filtering for report-cycle diagnostics,
5. portfolio-scope diagnostics through a JSONB GIN index,
6. status queue and recent-update scans,
7. completion scans for future housekeeping,
8. request/job joins,
9. append-only event history by job and event creation time,
10. snapshot lookup by job and recent snapshot support diagnostics,
11. upstream-lineage lookup by snapshot id,
12. upstream service and endpoint diagnostics by supportability posture and creation time,
13. archive document lookup for support diagnostics after successful `lotus-archive` handoff.

`make migration-smoke` checks that the implementation-backed indexes exist.

## Partitioning And Housekeeping Posture

Native PostgreSQL partitioning is deliberately not enabled in the first ledger migration. Global
idempotency is a first-order correctness requirement, and PostgreSQL partitioned-table uniqueness
requires the partition key to participate in the unique constraint. Monthly range partitioning by
`created_at` must therefore wait for a later scale/retention RFC that introduces either a global
idempotency registry table or a governed partition-aware idempotency strategy.

The ledger is partition-ready because it uses deterministic IDs, time-based operational indexes,
append-only event records, and forward-only migrations. The first wave does not provide a destructive
purge endpoint, legal-hold handling, or document-retention semantics; those remain owned by
`lotus-archive`. The report ledger records only archive handoff request/document identifiers and
truthful success/failure posture. Future housekeeping jobs must preserve request/job/event lineage
and must not delete records needed for audit, reconciliation, idempotency, archive lookup, or
support diagnostics.

## Future Upgrade Path

Future migrations must:

1. be versioned and forward-only,
2. be deterministic under repeated application,
3. preserve report request lineage and append-only status-event history,
4. include explicit index, uniqueness, and foreign-key validation when new support paths are added,
5. document any operational backfill, retention, archive, or replay implications.
