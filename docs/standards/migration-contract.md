# Migration Contract Standard

- Service: `lotus-report`
- Persistence mode: **PostgreSQL report job ledger schema and report batch ledger schema** for
  durable reporting request, job, status, batch, and batch-item lifecycle state.
- Migration policy: **forward-only schema management** with deterministic smoke validation.

## Deterministic Checks

- `make migration-smoke` validates that this contract document exists, applies the versioned
  PostgreSQL report job ledger schema and report batch ledger schema, checks mandatory tables `report_request`,
  `report_job`, `report_status_event`, `report_input_snapshot`, `report_upstream_call`,
  `report_batch`, and `report_batch_item`, verifies required operational indexes, and verifies
  database-level idempotency uniqueness on
  `report_request.idempotency_key` plus the single-snapshot-per-job uniqueness posture on
  `report_input_snapshot.report_job_id` and batch idempotency uniqueness on
  `report_batch.idempotency_key`. It also verifies the archive handoff fields
  `archive_request_id`, `archive_document_id`, and `archive_completed_at`, the archive document
  lookup index, the archive-aware status/failure-category constraints used by PDF report jobs, and
  RFC-0104 batch dispatch fields `report_job_id`, `lease_owner`, `lease_token`,
  `lease_acquired_at`, `lease_expires_at`, `last_heartbeat_at`, and `dispatched_at`, plus
  RFC-0104 batch control/recovery fields `attempt_count`, `retry_eligible`, `next_retry_at`,
  `last_error_category`, `last_error_summary`, lifecycle timestamps, expanded batch/item status
  constraints, and retry lookup indexing.
- CI executes `make migration-smoke` on each PR against a dedicated PostgreSQL service container.
- Local migration smoke requires `REPORT_JOB_LEDGER_DATABASE_URL` and must not fall back to a file
  database. SQLite is retained only as an isolated unit-test adapter for fast ledger behavior tests.
- `000_report_status_event_legacy_contract_preflight.sql` is an additive compatibility preflight
  for existing PostgreSQL volumes that already contain the pre-contract `report_status_event`
  table. It must sort before `001_report_job_ledger.sql` and add the status-event contract columns
  before any dependent status-event indexes are created. Fresh databases continue to get the full
  current table shape from `001_report_job_ledger.sql`.
- `make migration-upgrade-smoke` creates an isolated PostgreSQL schema, seeds the supported
  `report-status-event-pre-contract-v0` baseline and a representative legacy event, runs the same
  migration function used by the API, batch worker, and scheduler twice, and verifies the
  `report-ledger-v1` columns, backfill values, indexes, row preservation, and deterministic replay.
  The isolated schema is removed transactionally; the target database's `public` schema and local
  Report volume are not reset by this check.

## Supported Upgrade And Failure Posture

- Supported source baseline: `report-status-event-pre-contract-v0` with the complete legacy
  status-event identity, lifecycle, actor, timestamp, correlation, and trace columns.
- Current target: `report-ledger-v1` with typed `event_schema_version`, `event_family`,
  `event_payload_json`, and optional `event_idempotency_key` fields.
- The API, batch worker, and scheduler all run `python -m app.runtime_schema` before their process
  entrypoint. The guard serializes migration through the Report advisory lock and uses the shared
  `app.reporting_persistence` migration owner.
- An unrecognized legacy shape fails before migration with exit code `78` and a stable diagnostic
  beginning `lotus_report_schema_startup_failed:report_schema_upgrade_unsupported`. The diagnostic
  names the target schema, affected table, and missing or incompatible columns without including a
  database URL, credential, SQL statement, or row payload.
- Do not remove the PostgreSQL volume as the default recovery action. Preserve the volume, capture
  the stable diagnostic, compare the existing shape with the supported baseline, and forward-fix
  or restore through the approved database recovery process.

This design adopts PostgreSQL's additive column behavior for constant defaults and the existing
Report advisory-lock boundary. See the PostgreSQL 16 guidance for
[adding columns](https://www.postgresql.org/docs/16/ddl-alter.html) and
[advisory locks](https://www.postgresql.org/docs/16/functions-admin.html). It deliberately rejects
destructive reset, a UI fallback, and a separate migration service.

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
14. batch lookup by creation time and status,
15. batch tenant/region/time filtering for operations,
16. batch item ordering by batch,
17. batch item portfolio diagnostics,
18. batch item status scans,
19. batch item lease-expiry scans for stale in-flight work,
20. batch item report-job lookup for dispatch reconciliation,
21. batch item retry eligibility and due-time scans for bounded recovery.

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
