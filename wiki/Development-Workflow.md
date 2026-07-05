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

## Review-to-issue workflow

- use `docs/architecture/CODEBASE-REVIEW-PLAYBOOK.md` before starting a codebase review slice
- search all GitHub issues for duplicate findings before filing or fixing review work
- track active validated findings in GitHub issues, with #109 as the current discovery ledger
- use `docs/architecture/CODEBASE-REVIEW-LEDGER.md` for historical review evidence and linked
  closure manifests, not as a local-only backlog
- include evidence, expected direction, acceptance criteria, duplicate-search proof, and validation
  proof in issue-discovery findings
