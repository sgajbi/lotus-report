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
  migration smoke, integration tests, e2e tests, coverage, security audit
- `make migration-upgrade-smoke`
  isolated real-PostgreSQL upgrade proof from `report-status-event-pre-contract-v0` to
  `report-ledger-v1`, including legacy-row preservation and deterministic rerun
- `make ci-local`
  local alias for the full repo CI gate
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
  enforces the repo's 99% coverage floor across unit, integration, and e2e packs

## Contract emphasis

- integration capability query semantics matter because downstream consumers rely on them
- reporting orchestration changes should be reviewed for cross-app impact
- summary/review request-convention compatibility should be documented truthfully when changed
