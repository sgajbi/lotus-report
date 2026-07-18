# Validation and CI

## Lane model

`lotus-report` uses:

1. `Remote Feature Lane`
2. `Pull Request Merge Gate`
3. `Main Releasability Gate`

## Local command mapping

- `make check`
  lint, typecheck, OpenAPI gate, unit tests
- `make ci`
  automation-oriented migration, integration, e2e, coverage, and security proof against a
  caller-owned isolated PostgreSQL database
- `make migration-upgrade-smoke`
  isolated real-PostgreSQL upgrade proof from `report-status-event-pre-contract-v0` to
  `report-ledger-v1`, including legacy-row preservation and deterministic rerun
- `make ci-local`
  preferred workstation gate; creates one temporary PostgreSQL database, runs `make ci`, and drops
  only the helper-owned database on success or failure
- `make docker-build`
  container build validation
- `scripts/rfc_0104_slice4_live_evidence.py`
  PostgreSQL-backed live evidence for internal RFC-0104 batch dispatch primitives; requires
  `REPORT_JOB_LEDGER_DATABASE_URL`
- canonical Docker front-office proof
  run from `lotus-workbench` with
  `scripts/live/Start-LotusFrontOfficeCanonical.ps1 -CleanCoreState -BuildImages -RunValidation`
  when a change must be proven against the production-shaped local stack.
- Docker runtime readiness proof
  `docker compose up -d --build lotus-report lotus-report-batch-worker lotus-report-batch-scheduler`
  against both a fresh volume and a preserved supported prior-schema volume, then verify
  `http://report.dev.lotus/health/ready` returns 200 and the PostgreSQL schema includes
  `report_job`, `report_batch`, `report_input_snapshot`, `report_upstream_call`, and the typed
  status-event contract. A destructive volume reset is not upgrade evidence.

## What the gates protect

- `make check`
  fast proof that lint, typing, OpenAPI quality, and unit behavior still match repo truth
- `make ci`
  PR-grade proof that integration, e2e, coverage, fresh/current schema, supported prior-schema
  upgrade, and security posture still hold
- combined coverage gate
  enforces the repo's 97% coverage floor across unit, integration, and e2e packs

## Safe local database lifecycle

Keep the canonical Report API, worker, and scheduler on their normal `lotus_report` database and
run local PR-grade proof through the isolation helper:

```powershell
$env:REPORT_JOB_LEDGER_DATABASE_URL="postgresql://lotus_report:lotus_report@localhost:5439/lotus_report"
make ci-local
```

The configured role must be allowed to create and drop databases. The helper derives a bounded,
unique database name, never runs stateful test suites against the source database, suppresses DSN
output, terminates only connections to the database it owns, and drops that exact database in a
guaranteed cleanup path. `make ci` remains the automation primitive for GitHub Actions and other
callers that already own an isolated database.

## Contract emphasis

- integration capability query semantics matter because downstream consumers rely on them
- reporting orchestration changes should be reviewed for cross-app impact
- summary/review request-convention compatibility should be documented truthfully when changed
