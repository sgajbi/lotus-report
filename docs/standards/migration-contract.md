# Migration Contract Standard

- Service: `lotus-report`
- Persistence mode: **report job ledger schema** for durable reporting request, job, and status
  lifecycle state.
- Migration policy: **forward-only schema management** with deterministic smoke validation.

## Deterministic Checks

- `make migration-smoke` validates that this contract document exists, that the durable ledger
  schema can be created in a disposable SQLite database, and that the mandatory tables exist:
  `report_request`, `report_job`, and `report_status_event`.
- CI executes `make migration-smoke` on each PR.

## Rollback and Forward-Fix

- Runtime rollback is not implemented for the first durable ledger wave.
- Any contract issue is resolved through **forward-fix** in code/docs and re-run of CI gates.
- Destructive schema changes require a later RFC or ADR with explicit migration and archive impact.

## Future Upgrade Path

When the ledger moves beyond the embedded first-wave persistence model:

1. add versioned migrations,
2. add deterministic migration apply checks in CI,
3. keep forward-only migration policy with explicit rollback strategy documented,
4. preserve report job lineage and append-only status-event history.
