# Repository Engineering Context

This file provides repository-local engineering context for `lotus-report`.

For platform-wide truth, read:

1. `../lotus-platform/context/LOTUS-QUICKSTART-CONTEXT.md`
2. `../lotus-platform/context/LOTUS-ENGINEERING-CONTEXT.md`
3. `../lotus-platform/context/CONTEXT-REFERENCE-MAP.md`

## Repository Role

`lotus-report` is the reporting and aggregation service for Lotus.

It builds reporting-oriented read models and reporting payloads from authoritative upstream services.

## Business And Domain Responsibility

This repository owns:

1. reporting read-model aggregation,
2. report summary payloads,
3. first-class portfolio review report payloads for client/advisor review meetings,
4. reporting metadata and download-reference contracts.

It is not the domain authority for core portfolio or analytics truth; it composes them for reporting use.

## Current-State Summary

Current repository posture:

1. `lotus-report` composes summary and review payloads from `lotus-core`, `lotus-performance`, and `lotus-risk`,
2. it is part of the canonical front-office stack and is exposed through `report.dev.lotus`,
3. it carries repo-native RFC-0084 consumer declarations for governed core domain data products
   used by reporting payloads,
4. it carries the RFC-0091 repo-native producer declaration and telemetry fixture for
   `ClientReportEvidencePack`,
5. `POST /reports/portfolios/{portfolio_id}/review` is the RFC-0002 first-class portfolio review
   report contract with typed request/response models, explicit client section readiness,
   advisor-only discussion sections, evidence lineage, and implementation-backed capability keys,
6. `POST /reports/portfolio-reviews`, `GET /reports/jobs`, `GET /reports/jobs/{job_id}`,
   `GET /reports/jobs/{job_id}/events`, and `POST /reports/jobs/{job_id}/cancel` are the RFC-0100
   durable report job ledger foundation for gateway-first initiation, PostgreSQL-backed
   idempotency, operator-safe job search, product-safe status, append-only event history,
   database-aware readiness, and bounded cancellation before `rendering`,
7. RFC-0101 now adds durable `report_input_snapshot` and `report_upstream_call` persistence for
   immutable report input capture, append-only upstream lineage, canonical hashing, support-safe
   evidence APIs, PostgreSQL-backed migration smoke coverage, and readiness posture linked to
   RFC-0100 jobs,
8. RFC-0102 now adds governed render-package assembly, lotus-render submission for PDF jobs,
   persisted render metadata on report-job status, and render-aware completion/failure posture
   while keeping replay, rerender, regenerate, and distribution out of scope,
9. RFC-0103 now adds `lotus-archive` handoff after successful PDF render completion, separate
   `archiving`/`archived` lifecycle states, and archive-aware status/failure posture while keeping
   retrieval, retention execution, legal hold, purge, and distribution owned by `lotus-archive`,
10. RFC-0104 is implemented for first-wave scope. The implemented surface includes durable batch
   materialization/status/control APIs, deterministic schedule-cycle identity, dispatch/lease/
   back-pressure primitives, retry/recovery controls, the internal item execution bridge,
   bounded run-once and runtime-pass primitives, daemonized worker and scheduler processes,
   config-backed scheduler administration APIs, gateway exposure, and Workbench explicit
   single-portfolio batch operation. Schedule CRUD, Workbench scheduler-management, and
   entitlement-certified public scheduler runtime remain future scope,
11. RFC-0105 implementation has started with observability structure cleanup, cross-service trace
   propagation, first-wave report metrics, rerender/regenerate controls, and failed-work replay for
   failed retry-eligible report jobs and implementation-backed batch items. Runtime correlation,
   request, trace, structured-log, and safe operator lookup field vocabulary is owned in
   `src/app/observability.py`; bounded Prometheus metric vocabulary, implemented
   rerender/regenerate/replay command posture, source-backed attention scan metrics, reserved
   dedicated dashboard metrics, and high-cardinality label rejection are owned in
   `src/app/reporting_metrics.py`; later RFC-0105
   slices must extend those owners rather than adding one-off literal fields in routers, clients,
   dashboards, or operator APIs,
12. companion gateway PR `sgajbi/lotus-gateway#145` validates that the Workbench-facing gateway
   boundary preserves partial/unavailable section states and advisor-only separation,
13. CI is standardized but still lighter than some core domain services,
14. cross-app orchestration accuracy matters because reporting payloads summarize authoritative upstream state.

## Architecture And Module Map

Primary areas:

1. `src/app/`
   reporting API and aggregation logic.
2. `src/app/observability.py`
   runtime correlation/request/trace propagation, structured log fields, and safe RFC-0105
   operator lookup field vocabulary.
3. `src/app/reporting_metrics.py`
   RFC-0105 bounded Prometheus metric vocabulary, report operation metrics, batch worker/scheduler
   gauges, source-backed attention scan gauges, reserved replay/rerender/regenerate metric
   posture, and metric label governance.
4. `scripts/`
   migration, OpenAPI, and monetary-float governance.
5. `tests/`
   unit, integration, and e2e validation.
6. `docs/standards/`
   local standards and ownership guidance.
7. `wiki/`
   canonical authored source for repository wiki publication and reporting operator onboarding summaries.
8. `contracts/domain-data-products/`
   repo-native producer and consumer declarations for governed upstream domain data products and
   reporting evidence products.
9. `contracts/trust-telemetry/`
   repo-native RFC-0087/RFC-0091 trust telemetry snapshots for governed reporting products.
10. `src/app/reporting_jobs/`
   PostgreSQL runtime ledger plus an isolated SQLite unit-test adapter for report request/job/status
   lifecycle, idempotency, request hashing, status retrieval, and bounded cancellation for the first
   asynchronous reporting wave.
11. `src/app/reporting_lineage/`
   PostgreSQL runtime store plus an isolated SQLite unit-test adapter for durable report input
   snapshots, canonical snapshot hashing, immutable per-job capture, append-only upstream-call
   lineage, support-safe evidence query models, and readiness checks for RFC-0101.
12. `src/app/reporting_render/`
    render-package composition, lotus-render orchestration, and `lotus-archive` handoff for
    PDF-capable report jobs.
    `package_builder.py` owns the source-backed portfolio-review render package contract, while
    `service.py` owns job lifecycle orchestration, render submission, persisted render metadata,
    archive handoff, and render/archive failure mapping.
13. `src/app/report_batch_orchestrator/`
    RFC-0104 batch reporting orchestration boundary. Current slices own source-backed selector
    validation, durable batch/batch-item materialization, deterministic schedule-cycle
    materialization, scheduled idempotency identity, internal dispatch/lease/back-pressure
    primitives, report-job creation/reuse for leased items, idempotent duplicate prevention,
    internal bounded retry, pause/resume, cancellation-boundary, expired-lease recovery primitives,
    an internal execution bridge over the existing report-job, snapshot, render, and archive
    handoff pipeline, and an internal single-batch worker run primitive that combines recovery,
    dispatch, and item execution under explicit back-pressure inputs. The internal runtime pass can
    scan durable runnable batches and invoke that worker primitive for a bounded batch count, and
    the `lotus-report-batch-worker` process runs that pass continuously under configured interval,
    batch-count, lease, and back-pressure limits. The `lotus-report-batch-scheduler` process reads
    governed schedule configuration and materializes explicit-portfolio, all-active, and inline
    manifest scheduled batches through the durable ledger. Certified `POST /reports/batches`,
    batch status, batch control, `run-once`, schedule list, and schedule `run-due` APIs expose the
    materialization/status/control/single-batch-run/config-backed-scheduler subset while keeping
    full product runtime support disabled until later scheduler-management and certification slices
    are implemented and proven.

## Runtime And Integration Boundaries

Runtime model:

1. FastAPI reporting service,
2. consumed through `lotus-gateway` and reporting-oriented flows,
3. depends on `lotus-core`, `lotus-performance`, `lotus-risk`, `lotus-render`, and
   `lotus-archive` for PDF jobs.

Boundary rules:

1. upstream domain truth stays in the authoritative services,
2. this service owns reporting aggregation and reporting contract shape,
3. canonical service identity should be used for cross-app validation,
4. report-ready payloads must remain faithful to upstream evidence,
5. advisor-only review material must remain separated from client-ready report sections.

## Repo-Native Commands

Use these commands as the primary local contract:

1. install
   `make install`
2. fast local gate
   `make check`
3. PR-grade local gate
   `make ci`
4. feature-lane parity
   `make ci-local`
5. Docker build
   `make docker-build`
6. domain-data-product contract validation
   `make domain-product-validate`

## Validation And CI Expectations

`lotus-report` uses explicit CI lanes:

1. `Remote Feature Lane`
2. `Pull Request Merge Gate`
3. `Main Releasability Gate`

Important validation expectations:

1. OpenAPI, typecheck, migration smoke, and security audit are active,
2. migration smoke and CI integration proof use PostgreSQL through
   `REPORT_JOB_LEDGER_DATABASE_URL`; file databases are not runtime evidence for RFC-0100,
3. RFC-0101 snapshot storage uses the same governed PostgreSQL runtime database and extends
   migration smoke with `report_input_snapshot` and `report_upstream_call` table, index, and
   check-constraint proof,
4. split unit, integration, e2e, and coverage validation are part of the merge gate,
5. reporting orchestration changes should be evaluated for cross-app impact,
6. README and wiki changes should preserve truthful explanation of API request conventions,
   especially that the first-class portfolio review endpoint publishes snake_case request, query,
   and response fields only,
7. when a remaining public surface exposes mixed query or request-body conventions, wiki or
   onboarding docs should include at least one executable request example so operators and future
   agents do not normalize the wrong parameter shape by accident.

## Standards And RFCs That Govern This Repository

Most relevant current governance:

1. `../lotus-platform/rfcs/RFC-0050-core-data-analytics-and-reporting-service-boundaries.md`
2. `../lotus-platform/rfcs/RFC-0067-centralized-api-vocabulary-inventory-and-openapi-documentation-governance.md`
3. `../lotus-platform/rfcs/RFC-0071-centralized-environment-scoped-service-addressing-and-ingress-governance.md`
4. `../lotus-platform/rfcs/RFC-0072-platform-wide-multi-lane-ci-validation-and-release-governance.md`
5. `../lotus-platform/rfcs/RFC-0073-lotus-ecosystem-engineering-context-and-agent-guidance-system.md`
6. `docs/standards/data-model-ownership.md`

## Known Constraints And Implementation Notes

1. reporting quality depends on upstream contract fidelity; drift here can misstate portfolio or analytics reality,
2. the service is orchestration-heavy, so naming and payload clarity matter,
3. canonical `report.dev.lotus` identity should be used for real cross-app validation,
4. reporting work should update both code and orchestration docs when contracts change materially,
5. repo-local `wiki/` content should stay concise, operator-focused, and derived from repo truth
   rather than duplicating the full `docs/` tree,
6. the current repo-native domain-data-product declaration intentionally records only governed
   `lotus-core` dependencies approved for `lotus-report`; `lotus-performance` and `lotus-risk`
   reporting dependencies remain on the watchlist until their producer declarations explicitly
   approve `lotus-report` as a governed consumer.

## Context Maintenance Rule

Update this document when:

1. report payload ownership or major orchestration scope changes,
2. repo-native commands or CI expectations change,
3. upstream dependency posture changes materially,
4. canonical runtime identity or front-office integration role changes,
5. current request-convention compatibility or canonical parameter naming changes,
6. durable reporting job lifecycle, idempotency, or ledger persistence posture changes,
7. durable report input snapshot or upstream-call lineage persistence, hashing, readiness, API, or
   migration posture changes,
8. render-package composition, lotus-render integration, persisted render metadata, or job
   lifecycle semantics change,
9. report ledger database, readiness, migration, or CI proof posture changes,
10. current-state rollout posture changes,
11. RFC-0104 batch orchestration module, selector materialization, support posture, or
    planned-vocabulary scope changes.
12. RFC-0105 observability, metrics, dashboard, alert, operator API, replay, rerender, or
    regenerate support posture changes.

## Cross-Links

1. `../lotus-platform/context/LOTUS-QUICKSTART-CONTEXT.md`
2. `../lotus-platform/context/LOTUS-ENGINEERING-CONTEXT.md`
3. `../lotus-platform/context/CONTEXT-REFERENCE-MAP.md`
4. `../lotus-platform/context/Repository-Engineering-Context-Contract.md`
5. [Lotus Developer Onboarding](../lotus-platform/docs/onboarding/LOTUS-DEVELOPER-ONBOARDING.md)
6. [Lotus Agent Ramp-Up](../lotus-platform/docs/onboarding/LOTUS-AGENT-RAMP-UP.md)
