# Migration Contract Standard

- Service: `lotus-report`
- Persistence mode: **PostgreSQL report job ledger schema and report batch ledger schema** for
  durable reporting request, job, status, batch, and batch-item lifecycle state, and the
  **PostgreSQL `idea_evidence_intake` table** created by migration 024.
- Scope boundary for `idea_evidence_intake`: this contract governs the PostgreSQL **table**. The
  store production uses by default is still SQLite at `IDEA_EVIDENCE_INTAKE_LEDGER_PATH`, selected
  by `REPORT_IDEA_EVIDENCE_INTAKE_LEDGER_BACKEND`. A proven transfer exists
  (`scripts/transfer_idea_evidence_intake.py`), but no environment has run it, so this contract
  still says nothing about the durability of the store actually in use. Tracked as report#326.
- Migration policy: **forward-only schema management** with deterministic smoke validation.

## Deterministic Checks

- `make migration-upgrade-smoke` additionally asserts `idea_evidence_intake`'s column **types** and
  indexes against `information_schema` — `jsonb` for both payloads, `timestamptz` for both instants.
  Types rather than presence, because a table created with `text` columns everywhere would satisfy a
  presence check while delivering neither shape validation on write nor instant-ordered timestamps,
  which are the two reasons for moving the store at all.

- `make migration-smoke` validates that this contract document exists, applies the versioned
  PostgreSQL report job ledger schema and report batch ledger schema, checks mandatory tables `report_request`,
  `report_job`, `report_status_event`, `report_job_work_item`, `report_input_snapshot`, `report_upstream_call`,
  `report_batch`, `report_batch_item`, and `idea_evidence_intake`, verifies required operational
  indexes, and verifies
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
  It also verifies the report-job work queue's one-item-per-job uniqueness, lease/completion shape,
  runnable-work index, and expired-lease recovery index introduced by
  `011_report_job_work_queue.sql`.
- CI executes `make migration-smoke` on each PR against a dedicated PostgreSQL service container.
- Direct local migration smoke requires `REPORT_JOB_LEDGER_DATABASE_URL` and must not fall back to
  a file database. Full workstation proof uses `make ci-local`, which creates one uniquely named
  database on the configured PostgreSQL server, runs the repository CI contract there, and drops
  only that helper-owned database on success or failure. `make ci` callers must already own an
  isolated database. SQLite is retained only as an isolated unit-test adapter for fast ledger
  behavior tests.
- `000_report_status_event_legacy_contract_preflight.sql` is an additive compatibility preflight
  for existing PostgreSQL volumes that already contain the pre-contract `report_status_event`
  table. It must sort before `001_report_job_ledger.sql` and add the status-event contract columns
  before any dependent status-event indexes are created. Fresh databases continue to get the full
  current table shape from `001_report_job_ledger.sql`.
- `make migration-upgrade-smoke` creates an isolated PostgreSQL schema, seeds the supported
  `report-status-event-pre-contract-v0` baseline and a representative legacy event, runs the same
  migration function used by the API, report job worker, batch worker, and scheduler twice, and verifies the
  `report-ledger-v1` columns, exact type/nullability contract, backfill values, indexes, row
  preservation, and that the first run applies the complete ordered file set while the second run
  consults the applied-migration ledger and applies nothing. It also creates four isolated wrong-nullability current
  shapes and proves each is rejected before mutation. The isolated schemas are removed
  transactionally; the target database's `public` schema and local Report volume are not reset by
  this check.

## Applied-Migration Ledger

- The runner (`apply_report_schema_migrations`) keeps an applied-migration ledger in
  `report_schema_migration` (`migration_name` primary key, `applied_at timestamptz`),
  bootstrapped by the runner itself rather than by a migration file, and executes only files not
  recorded there. A current-schema restart performs no migration DDL; each migration's DDL
  executes once per database (report#376).
- The whole run — ledger bootstrap, every pending file, and its ledger rows — is one caller-owned
  transaction, serialized across concurrent startups by a transaction-scoped advisory lock keyed
  separately from the `app.runtime_schema` session lock. An interrupted run rolls back
  completely, ledger rows included, and the next run recomputes the same pending set.
- A database migrated before the ledger existed has no `report_schema_migration` table. Its first
  startup on ledger-aware code performs one bridge replay of every file — exactly the retired
  per-boot behavior, which the historical migrations were written to converge under — and records
  the set; replay then never happens again. No operator action is required for that rollout, and
  a pre-ledger binary restarting later simply replays as it always did, so the overlap window of
  a rolling deployment is safe in both directions.
- Migration filenames are the ledger identity and are immutable once merged: renaming a shipped
  file makes it look unapplied and replays it. Editing a shipped file's content does not re-run
  it on databases that already recorded it; a fix that must reach existing databases needs a new
  forward migration.
- A pending file that sorts before a recorded one is refused before mutation with
  `report_schema_migration_out_of_order` — interleaving a file below merged history is edited
  history, not work. Recorded names with no matching local file are tolerated: forward-only
  additive migrations are the promise that an older binary may start against a newer schema.
- Deterministic repeated application now has two layers: the ledger applies each file once, and
  each file must still be written to converge when re-run from scratch, because an interrupted,
  rolled-back run replays the whole pending set and the bridge replay re-runs everything once.
  New migrations therefore keep the existing convergent-DDL discipline even though steady-state
  replay is retired.
- Acceptance evidence is `tests/integration/test_migration_ledger_convergence.py`, against real
  populated PostgreSQL: empty applied set and unchanged `relfilenode` for every index on a
  current-schema restart, fresh-install versus legacy-upgrade schema-fingerprint equality, the
  one-time bridge replay preserving receipts and the identity index, concurrent startups observed
  serializing on the advisory lock with the loser applying nothing, interrupted-run rollback and
  retry, and the out-of-order refusal.

## Supported Upgrade And Failure Posture

- Supported source baseline: `report-status-event-pre-contract-v0` with the complete legacy
  status-event identity, lifecycle, actor, timestamp, correlation, and trace columns.
- Current target: `report-ledger-v1` with required typed `event_schema_version`, `event_family`, and
  `event_payload_json` fields plus optional typed `event_idempotency_key`. Existing columns with
  different required/optional nullability are unsupported because additive `IF NOT EXISTS`
  migrations cannot repair them safely.
- The API, report job worker, batch worker, and scheduler all run `python -m app.runtime_schema`
  before their process entrypoint. The guard serializes migration through the Report advisory lock
  and uses the shared `app.reporting_persistence` migration owner.
- An unrecognized legacy shape fails before migration with exit code `78` and a stable diagnostic
  beginning `lotus_report_schema_startup_failed:report_schema_upgrade_unsupported`. The diagnostic
  names the target schema, affected table, and missing, type-incompatible, or
  nullability-incompatible columns without including a database URL, credential, SQL statement, or
  row payload.
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
14. report-job work lookup by job,
15. runnable report-job work by status, availability, and creation time,
16. report-job work lease-expiry scans for stale in-flight work,
17. batch lookup by creation time and status,
18. batch tenant/region/time filtering for operations,
19. batch item ordering by batch,
20. batch item portfolio diagnostics,
21. batch item status scans,
22. batch item lease-expiry scans for stale in-flight work,
23. batch item report-job lookup for dispatch reconciliation,
24. batch item retry eligibility and due-time scans for bounded recovery.

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
2. converge under repeated application — the applied-migration ledger retires steady-state
   replay, but an interrupted run and the pre-ledger bridge both replay files from scratch,
3. preserve report request lineage and append-only status-event history,
4. include explicit index, uniqueness, and foreign-key validation when new support paths are added,
5. document any operational backfill, retention, archive, or replay implications.
