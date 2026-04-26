# RFC Traceability Map

This document provides explicit implementation evidence pointers for active RFCs.

## RFC-0001 - Test Pyramid Rebalance and Meaningful Coverage Hardening

- Test pyramid and coverage evidence:
  - `tests/unit/`
  - `tests/integration/`
  - `tests/e2e/`
  - `Makefile` (`test-pyramid`, `test-coverage`, `ci`)
  - `.github/workflows/pr-merge-gate.yml`

## RFC-0002 - First-Class Portfolio Review Report Endpoint

- Planning and research evidence:
  - `rfcs/RFC-0002-first-class-portfolio-review-report-endpoint.md`
  - `docs/supported-features.md`
  - `src/app/routers/reports.py` (`/reports/portfolios/{portfolio_id}/review`)
  - `src/app/services/reporting_read_service.py`
  - `contracts/domain-data-products/lotus-report-products.v1.json`
- Implementation evidence added during rollout:
  - typed report request/response contracts in `src/app/models/contracts.py`
  - review route contract tests in `tests/integration/test_api.py`
  - implementation-backed capability publication in `GET /integration/capabilities`
  - section, performance, risk, evidence, and lineage tests in
    `tests/unit/test_reporting_read_service.py` and
    `tests/unit/test_reporting_read_service_additional.py`
  - advisor discussion builder tests in `tests/unit/test_portfolio_review_advisor.py`
  - gateway readiness proof in `sgajbi/lotus-gateway#145`
  - OpenAPI/vocabulary validation output
  - supported-features rows promoted from `planned` to `implementation-backed` only after
    implementation evidence exists
  - repo-local wiki usage examples
  - closure context updates in `REPOSITORY-ENGINEERING-CONTEXT.md`, `README.md`, and `wiki/`

## RFC-0104 - Batch Reporting Scheduler, Concurrency, And Recovery

- Slice 1 cleanup and structure evidence:
  - `src/app/report_batch_orchestrator/` is the dedicated future batch orchestration module
    boundary.
  - `src/app/report_batch_orchestrator/contracts.py` centralizes RFC-0104 selector and frequency
    vocabulary while keeping `BATCH_RUNTIME_SUPPORTED` false.
  - `tests/unit/report_batch_orchestrator/test_boundary.py` verifies the boundary vocabulary and
    prevents `docs/supported-features.md` from claiming implementation-backed batch runtime
    support before the durable ledger, scheduler, worker, APIs, and proof exist.
  - `docs/supported-features.md` records RFC-0104 batch orchestration and batch scheduler rows as
    `planned`, not implementation-backed.
  - `wiki/Operations-Runbook.md` records that operators must continue to use individual report-job
    APIs until RFC-0104 runtime slices are implemented and proven.
- Slice 2 batch ledger, selectors, and idempotent materialization evidence:
  - `src/app/report_batch_orchestrator/models.py`, `selector.py`, `ledger.py`, and
    `postgres_ledger.py` implement source-backed explicit-list and selected-subset materialization
    plus duplicate-safe batch creation.
  - `migrations/007_report_batch_ledger.sql` adds `report_batch` and `report_batch_item` with
    idempotency uniqueness, batch/item uniqueness, status constraints, and operational indexes.
  - `tests/unit/report_batch_orchestrator/test_batch_ledger.py` proves selector validation,
    deterministic materialization order, idempotent duplicate submission, and incompatible-request
    conflict behavior.
  - `tests/integration/test_postgres_report_batch_ledger.py` proves PostgreSQL batch ledger parity
    when `REPORT_JOB_LEDGER_DATABASE_URL` is available.
  - `docs/standards/batch-orchestration-source-map.md` records the source mapping and remaining
    source gaps for all-active and manifest selectors.
- Slice 3 deterministic schedule materialization evidence:
  - `src/app/report_batch_orchestrator/schedule.py` materializes monthly, quarterly, semi-annual,
    yearly, and explicit production cycles from a business as-of date and output contract versions.
  - `BatchCycleRequest` and `BatchCycle` in `src/app/report_batch_orchestrator/models.py` define
    the internal schedule contract without exposing an operator-facing scheduler API.
  - `tests/unit/report_batch_orchestrator/test_schedule.py` proves period/as-of semantics,
    unsupported-frequency rejection, explicit-period validation, scheduled-batch idempotency
    stability, template-version sensitivity, and continued all-active selector gating.
  - Runtime posture remains intentionally disabled through `BATCH_RUNTIME_SUPPORTED = False`;
    no scheduler loop, worker dispatch, retry, pause, resume, or recovery API is shipped by this
    slice.
- Slice 4 internal dispatch, lease, and back-pressure evidence:
  - `src/app/report_batch_orchestrator/dispatch.py` implements internal batch-item dispatch under
    explicit active-batch, active-item, upstream, render, and archive pressure limits.
  - `src/app/report_batch_orchestrator/ledger.py` and `postgres_ledger.py` persist report-job
    linkage, lease owner/token/acquired/expires/heartbeat timestamps, dispatch timestamp, active
    counts, and stale-lease protection.
  - `migrations/007_report_batch_ledger.sql` adds PostgreSQL dispatch columns and indexes for
    lease-expiry and report-job lookup.
  - `tests/unit/report_batch_orchestrator/test_dispatch.py` proves back-pressure reasons, one
    report job per leased batch item, duplicate dispatch prevention, active batch limits, lease
    heartbeat/expiry, stale-token rejection, and concurrent worker duplicate prevention.
  - `tests/integration/test_postgres_report_batch_ledger.py` proves the same dispatch and lease
    behavior against PostgreSQL when `REPORT_JOB_LEDGER_DATABASE_URL` is available.
  - `scripts/rfc_0104_slice4_live_evidence.py` produces Docker/PostgreSQL-backed live evidence
    showing internal dispatch creates exactly one durable report job per batch item.
  - Live stack proof was run against the canonical Docker front-office topology with
    `lotus-report` backed by `lotus-report-postgres`, exposed through `report.dev.lotus`, and
    validated through `lotus-workbench/scripts/live/Start-LotusFrontOfficeCanonical.ps1
    -CleanCoreState -BuildImages -RunValidation`.
  - Runtime posture remains intentionally disabled through `BATCH_RUNTIME_SUPPORTED = False`;
    this slice does not ship a scheduler loop, worker process, operator-facing batch API, retry,
    pause, resume, cancel, or recovery capability.
- Slice 5 internal retry, control, and recovery evidence:
  - `src/app/report_batch_orchestrator/models.py` extends the durable batch and item state model
    with paused, cancelled, completed, completed-with-failures, failed, succeeded,
    failed-retryable, failed-terminal, cancelled, and recovery-pending states plus attempt,
    retry, failure, and lifecycle timestamps.
  - `migrations/007_report_batch_ledger.sql` persists the Slice 5 control and recovery fields in
    PostgreSQL and adds retry-oriented lookup support without replacing the existing dispatch
    schema.
  - `src/app/report_batch_orchestrator/ledger.py` and `postgres_ledger.py` implement bounded
    retry-failed-only reset, pause, resume, cancellation boundaries, expired-lease recovery, and
    aggregate batch status reconciliation for the SQLite unit-test adapter and PostgreSQL runtime
    ledger.
  - Retry reset is intentionally limited to retryable failed items without an attached
    `report_job_id`; this prevents duplicate report-job creation when a failure occurs after
    report-job handoff.
  - `tests/unit/report_batch_orchestrator/test_dispatch.py` proves pause blocks dispatch until
    resume, retry resets only due eligible failed items, retry does not requeue items with existing
    report jobs, cancellation leaves items with created report jobs untouched, and expired-lease
    recovery is idempotent before redispatch.
  - `tests/integration/test_postgres_report_batch_ledger.py` proves the same control and recovery
    primitives against PostgreSQL when `REPORT_JOB_LEDGER_DATABASE_URL` is available.
  - `docs/standards/batch-orchestration-source-map.md`, `docs/supported-features.md`, this
    traceability map, and `wiki/Operations-Runbook.md` distinguish internal primitives from
    operator-supported batch runtime capability.
  - Runtime posture remains intentionally disabled through `BATCH_RUNTIME_SUPPORTED = False`;
    this slice does not ship a scheduler loop, worker process, operator-facing batch API, or
    certified recovery operator capability.
- Slice 6 certified materialization, status, and control API evidence:
  - `src/app/routers/report_batches.py` exposes certified `lotus-report` batch APIs for
    `POST /reports/batches`, `GET /reports/batches/{batch_id}`, `POST
    /reports/batches/{batch_id}:pause`, `:resume`, `:cancel`, `:retry-failed`, and
    `:recover-expired-leases`.
  - `src/app/report_batch_orchestrator/models.py` defines product-safe request/response contracts,
    full OpenAPI examples, and attribute descriptions for the batch API surface.
  - `src/app/routers/caller_context.py` centralizes caller-context header validation shared by
    report-job and report-batch APIs, reducing duplicate error handling.
  - `src/app/report_batch_orchestrator/service.py` wires the PostgreSQL batch ledger through the
    same dependency pattern used by report jobs.
  - `tests/integration/test_report_batch_api.py` proves idempotent batch creation, status lookup,
    pause/resume/retry/recovery/cancel controls, conflict handling, missing caller context,
    selector validation errors, not-found errors, and OpenAPI example/description quality.
  - `tests/integration/test_api.py` proves integration capabilities now publish
    `lotus-report.reporting.batch_materialization_api.v1` and
    `lotus-report.reporting.batch_control_api.v1`.
  - `docs/supported-features.md` promotes only the certified materialization/status/control API
    keys to implementation-backed. Full batch orchestration and scheduler capability remain
    planned.
  - Local PostgreSQL and runtime proof on 2026-04-26:
    - `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report make ci`
      passed with lint, format, monetary-float guard, mypy, OpenAPI quality, PostgreSQL migration
      smoke, integration, e2e, 99% coverage, and security audit.
    - `make docker-build` passed for `lotus-report:ci-test`.
    - `docker compose up -d --build lotus-report` rebuilt the production-shaped local
      `lotus-report` container against healthy `lotus-report-postgres`.
    - Live Docker proof returned OpenAPI paths for `/reports/batches`, `/reports/batches/{batch_id}`,
      and all control endpoints, then materialized `rbch_b5dc820c412f4763bf9ccb4355755447` for
      `PB_SG_GLOBAL_BAL_001` with `status_counts={"materialized":1}`.
  - Runtime posture remains intentionally disabled through `BATCH_RUNTIME_SUPPORTED = False`;
    this slice does not ship a scheduler loop, worker process, dispatch operator API, gateway
    exposure, or Workbench UI.
- Slice 7 report-job, render, and archive integration evidence:
  - `src/app/report_batch_orchestrator/execution.py` implements the internal execution bridge for
    a dispatched batch item. It loads the linked RFC-0100 report job, runs the existing RFC-0101
    snapshot capture path when the job is still accepted, runs the existing RFC-0102 render and
    RFC-0103 archive handoff path for PDF jobs, and maps the final report-job outcome back to the
    durable batch item.
  - `src/app/report_batch_orchestrator/ledger.py` and `postgres_ledger.py` add a successful-item
    transition that clears lease and retry fields, records completion, and reconciles the aggregate
    batch status without changing the existing cancellation and retry boundaries.
  - `tests/unit/report_batch_orchestrator/test_execution.py` proves successful
    capture-render-archive completion, source-lineage preservation through the captured snapshot,
    archive metadata handoff, render validation failure propagation, archive storage failure
    propagation, and json-only data-ready completion.
  - `tests/integration/test_postgres_report_batch_ledger.py` proves the successful item-to-batch
    reconciliation path against PostgreSQL.
  - Local proof on 2026-04-26:
    - `make check` passed with lint, format, monetary-float guard, mypy, OpenAPI quality, and
      298 unit tests.
    - `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report make migration-smoke`
      passed against the Docker `lotus-report-postgres` service.
    - `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report make test-integration`
      passed with 84 PostgreSQL-backed integration tests.
    - `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report make ci`
      passed with lint, typecheck, OpenAPI quality, PostgreSQL migration smoke, integration, e2e,
      combined coverage at 99%, and security audit.
    - `make docker-build` built `lotus-report:ci-test`.
    - `docker compose up -d --build lotus-report` rebuilt the production-shaped local
      `lotus-report` service against healthy `lotus-report-postgres`; `GET /health` returned
      `{"status":"ok"}` on `localhost:8300`.
  - Runtime posture remains intentionally disabled through `BATCH_RUNTIME_SUPPORTED = False`;
    this slice does not ship a scheduler loop, background executor, dispatch operator API, gateway
    exposure, or Workbench UI.
- Slice 8 documentation, runbook, and supportability-floor evidence:
  - `README.md` now summarizes the certified internal batch materialization/status/control APIs,
    the internal item execution bridge, and the current unsupported scheduler/runtime/gateway/UI
    scope.
  - `wiki/Operations-Runbook.md` now gives direct operator posture for creating, inspecting,
    pausing, resuming, cancelling, retrying, and recovering internal batches, plus the
    observability floor and RFC-0105 deferrals.
  - `wiki/API-Surface.md` now includes copy-paste examples for all certified batch control
    endpoints and explicitly keeps gateway, Workbench, scheduling, and long-running runtime
    telemetry out of current scope.
  - `docs/supported-features.md` remains split between implementation-backed batch
    materialization/control/internal execution bridge features and planned scheduler/orchestration
    features.
  - Validation on 2026-04-26:
    - `python -m pytest tests/unit/report_batch_orchestrator/test_boundary.py -q` passed.
    - `git diff --check` passed.
    - `powershell -ExecutionPolicy Bypass -File ..\lotus-platform\automation\Sync-RepoWikis.ps1
      -CheckOnly -Repository lotus-report` reported expected publication drift for
      `API-Surface.md`, `Operations-Runbook.md`, `RFC-Index.md`, and pre-existing
      `Validation-and-CI.md`; wiki publication remains a post-merge action.
- Slice 9 implementation-proof evidence:
  - `tests/integration/test_report_batch_execution.py` proves an explicit-list batch item can move
    through PostgreSQL batch ledger dispatch, RFC-0100 report job creation, RFC-0101 snapshot
    persistence, RFC-0102 render orchestration, RFC-0103 archive handoff, and final batch-item and
    batch status reconciliation.
  - Existing integration and unit proof covers selected-subset materialization, duplicate
    idempotency, dispatch/back-pressure, pause/resume/cancel, retry-failed, expired-lease
    recovery, OpenAPI examples, and supported-features guardrails.
  - Validation on 2026-04-26:
    - `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report
      python -m pytest tests/integration/test_report_batch_execution.py -q` passed.
    - `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report
      make test-integration` passed with PostgreSQL-backed batch, job, snapshot, render, and archive
      integration coverage.
    - `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report
      make ci` passed with lint, format, monetary-float guard, mypy, OpenAPI quality, migration
      contract check, 85 integration tests, 6 e2e tests, 298 unit tests, combined 99% coverage, and
      security audit.
