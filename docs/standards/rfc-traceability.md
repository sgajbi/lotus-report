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
