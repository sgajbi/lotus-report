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

## Running tests while the local product stack is up

Every way of running the integration suite is safe alongside `docker compose up`:

- `pytest tests/integration` (and `make test-integration`, `make ci-local`) never touch the
  product database. When `REPORT_JOB_LEDGER_DATABASE_URL` names a PostgreSQL server, the test
  session provisions an ephemeral `lotus_report_ci_<token>` database on that server, points the
  suite at it, and drops it at session end (`tests/integration/conftest.py`). The batch worker,
  job worker, and scheduler containers write only to `lotus_report`, so they can neither corrupt a
  test run nor be corrupted by one.
- Cleanup is symmetric: dropping the ephemeral test database cannot remove the product runtime,
  and `docker compose down` on the product stack cannot remove a test database mid-run beyond
  taking the whole server down with it.
- With `REPORT_JOB_LEDGER_DATABASE_URL` unset, the PostgreSQL-backed integration tests skip.
