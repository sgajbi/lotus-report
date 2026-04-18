# Development Workflow

## Branching and slice model

- branch from `main`
- keep one branch per RFC or documentation slice
- use PR-first delivery

## Repo-native commands

- `make check`
  fast local gate
- `make ci`
  PR-grade local proof
- `make ci-local`
  local alias for the repo CI contract
- `make docker-build`
  container build validation

## Documentation workflow

- keep `README.md` concise and operator-facing
- keep `wiki/` as the authored wiki source
- keep deep implementation detail in `docs/standards/`
- document request-convention differences truthfully instead of flattening them
