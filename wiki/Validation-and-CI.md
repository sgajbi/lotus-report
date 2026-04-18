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

## Contract emphasis

- integration capability query semantics matter because downstream consumers rely on them
- reporting orchestration changes should be reviewed for cross-app impact
- summary/review request-convention compatibility should be documented truthfully when changed
