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

## What the gates protect

- `make check`
  fast proof that lint, typing, OpenAPI quality, and unit behavior still match repo truth
- `make ci`
  PR-grade proof that integration, e2e, coverage, migration, and security posture still hold
- combined coverage gate
  enforces the repo's 99% coverage floor across unit, integration, and e2e packs

## Contract emphasis

- integration capability query semantics matter because downstream consumers rely on them
- reporting orchestration changes should be reviewed for cross-app impact
- summary/review request-convention compatibility should be documented truthfully when changed
