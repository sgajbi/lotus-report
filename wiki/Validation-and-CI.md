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
