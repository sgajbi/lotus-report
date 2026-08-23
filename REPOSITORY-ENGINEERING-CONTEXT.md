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
   durable report job and work-queue foundation for gateway-first acceptance, PostgreSQL-backed
   idempotency, atomic work enqueue, operator-safe job search, product-safe status, leased
   asynchronous execution with bounded retry, database-aware readiness, and bounded cancellation
   before `rendering`; the API returns `202 Accepted` after the durable transaction and the
   separate `lotus-report-job-worker` resumes source capture, render, and archive work. New lifecycle
   event rows carry `event_schema_version`, `event_family`, support-safe `event_payload`, and
   optional `event_idempotency_key`, while legacy rows remain readable as
   `report-status-event.legacy.v0`,
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
10. RFC-0023 Slice 11B adds report-side consumption for approved `lotus-advise`
   `proposal_narrative_package` payloads on `POST /reports/portfolio-reviews`: the package must be
   `INCLUDED_REVIEWED_NARRATIVE`, review-approved for advisor use, and source-hashed; `lotus-report`
   preserves it in the request hash, immutable snapshot, lineage summary, and render package
   without approving, rewriting, or inferring advisory content,
11. RFC-0024 Slice 9 adds report-side consumption for approved `lotus-advise`
   `proposal_memo_package` payloads on `POST /reports/portfolio-reviews`: the package must be
   `INCLUDED_ADVISOR_PROPOSAL_MEMO`, review-approved for advisor use, source-hashed, and
   client-ready blocked; `lotus-report` preserves it in the immutable snapshot, render package,
   and archive handoff metadata without approving, rewriting, or inferring memo facts,
12. RFC40-WTBD-004 first-wave report materialization is implemented for manage-owned
   `DpmProofPackReportInput`: `POST /reports/proof-packs` exposes a typed source-owned input
   schema, rejects missing hash/evidence/redaction/retention/supportability posture before durable
   capture, persists the bounded handoff as an immutable report snapshot, records lineage to
   `lotus-manage`, builds a `proof_pack` render package for `lotus-render` template `proof-pack
   v1`, and reuses the existing archive handoff lifecycle for PDF artifacts without recomputing
   proof-pack evidence,
13. RFC41-WTBD-008 first-wave wave report materialization is implemented for manage-owned
   `DpmWaveReportInput`: `POST /reports/rebalance-waves` exposes a typed source-owned input
   schema, rejects missing hash/evidence/redaction/retention/supportability posture before durable
   capture, persists the bounded handoff as an immutable report snapshot, records lineage to
   `lotus-manage`, builds a `rebalance_wave` render package for `lotus-render` template
   `rebalance-wave v1`, and reuses the existing archive handoff lifecycle for PDF artifacts
   without recomputing wave state, proof-pack linkage, supportability, internal handoff evidence,
   or external execution posture,
14. RFC40-WTBD-010 report-side portfolio-memory consumption is implemented for the first-wave DPM
   report jobs: proof-pack, rebalance-wave, and outcome-review report inputs are typed
   source-owned DPM schemas that require source hashes, evidence refs, redaction, retention, and
   supportability posture before durable capture. They may also carry a manage-owned bounded
   `portfolio_memory_context`, and `lotus-report` persists that context in immutable snapshot
   lineage and render-package lineage, including context hash, support boundary, event-ref
   limit/selection/returned/omitted/truncated posture, and per-ref event time/rank where supplied,
   without reconstructing manage-owned portfolio-memory events.
   `lotus-report` also owns a report source-event family at
   `GET /reports/jobs/{job_id}/portfolio-memory-events` that maps report lifecycle, snapshot,
   render, and archive evidence into support-safe event identities, source refs, artifact refs,
   content hashes, and retention/redaction/access/audit policy without exposing raw snapshot
   payloads or storage references. This closes only the report-owned source-event family; AI, OMS,
   PM-scoring, and client-communication source-event families remain separate owner work,
14. RFC-0104 is implemented for first-wave scope. The implemented surface includes durable batch
   materialization/status/control APIs, deterministic schedule-cycle identity, dispatch/lease/
   back-pressure primitives, retry/recovery controls, the internal item execution bridge,
   bounded run-once and runtime-pass primitives, daemonized worker and scheduler processes,
   config-backed scheduler administration APIs, gateway exposure, and Workbench explicit
   single-portfolio batch operation. Schedule CRUD, Workbench scheduler-management, and
   entitlement-certified public scheduler runtime remain future scope,
15. RFC-0105 implementation has started with observability structure cleanup, cross-service trace
   propagation, first-wave report metrics, rerender/regenerate controls, and failed-work replay for
   failed retry-eligible report jobs and implementation-backed batch items. Runtime correlation,
   request, trace, structured-log, and safe operator lookup field vocabulary is owned in
   `src/app/observability.py`; bounded Prometheus metric vocabulary, implemented
   rerender/regenerate/replay command posture, source/derived job relationship posture,
   recent support-safe rerender attempt diagnostics for correction-document audit,
   source-backed attention scan metrics, reserved dedicated dashboard metrics, and
   high-cardinality label rejection are owned in
   `src/app/reporting_metrics.py`; later RFC-0105
   slices must extend those owners rather than adding one-off literal fields in routers, clients,
   dashboards, or operator APIs,
16. Docker-local `lotus-report` startup now initializes and verifies the PostgreSQL report-job
   ledger, report-work queue, and report-input snapshot schema before serving readiness. The API,
   report job worker, batch worker, and scheduler containers all use the same schema guard and shared
   `src/app/reporting_persistence/`
   migration owner. Supported `report-status-event-pre-contract-v0` volumes upgrade to
   `report-ledger-v1` in place; unrecognized shapes fail before mutation with a stable
   `lotus_report_schema_startup_failed:report_schema_upgrade_unsupported` diagnostic, including
   existing contract columns with incompatible PostgreSQL types or required/optional nullability. The
   real-PostgreSQL `make migration-upgrade-smoke` fixture proves legacy-row preservation,
   contract backfill, index creation, exact contract nullability, invalid-nullability rejection,
   and deterministic rerun without resetting `public`,
17. PostgreSQL-backed report-job, report-batch, and report-input snapshot/upstream-call adapters
   share the bounded process-local provider in `src/app/postgres.py`; adapters own transaction
   units while the provider owns connection reuse, max concurrency, acquisition timeout, connect
   timeout, statement timeout, application name, and deterministic shutdown. Database-URL-backed
   scripts own their adapters with context managers. Integration tests register directly created
   adapters through `tests/integration/postgres_adapter_ownership.py`, whose per-test scope closes
   them in reverse creation order. Adapters returned by the shared runtime provider must not be
   registered as test-owned or closed by request-scoped code,
18. companion gateway PR `sgajbi/lotus-gateway#145` validates that the Workbench-facing gateway
   boundary preserves partial/unavailable section states and advisor-only separation,
19. `contracts/idea-evidence-intake/lotus-report-idea-evidence-pack-intake.v1.json` records the
   implemented, not-certified `lotus-idea` evidence-pack intake route boundary for
   `ClientReportEvidencePack`; it proves only source-safe route intake through
   `POST /reports/idea-evidence-packs` and does not prove report materialization, render, archive,
   client-publication authority, or supported-feature promotion. Intake idempotency is durable in
   a SQLite ledger configured by `IDEA_EVIDENCE_INTAKE_LEDGER_PATH`; records store support-safe
   payload fingerprints, source identifiers, caller context, correlation id, and trace id without
   raw evidence payloads. The companion Report-owned retention policy contract is enforced before
   intake or materialization; legal-hold posture is propagated through report-job options for the
   Archive handoff,
20. `contracts/idea-evidence-materialization/lotus-report-idea-evidence-pack-materialization.v1.json`
   records the implemented, not-certified `lotus-idea` evidence-pack materialization route
   boundary for `ClientReportEvidencePack`: `POST /reports/idea-evidence-packs/materializations`
   accepts reviewed idea evidence plus report-owned portfolio scope, creates a governed proof-pack
   report job, preserves immutable lineage to `lotus-idea`, invokes the existing render/archive
   lifecycle for PDF output, returns a typed source-safe materialization receipt with
   report-package identity, source authority, render/archive outcome posture and identifiers, and
   keeps suitability, mandate approval, execution, distribution, client-publication authority, and
   supported-feature promotion blocked,
21. CI is standardized but still lighter than some core domain services,
22. direct local debugging is explicitly scoped to `ENTERPRISE_RUNTIME_PROFILE=local`; production-like
   profiles (`prod`, `production`, `preprod`, `staging`, and `uat`) fail closed by enforcing read
   and write authorization and failing startup validation when write/read authz or primary key
   identity material is missing,
22. shared downstream retry behavior is owned in `src/app/clients/http_resilience.py`; retries cover
   transport failures plus transient HTTP statuses `429`, `502`, `503`, and `504` within
   `UPSTREAM_MAX_RETRIES`, while validation, authorization, not-found, conflict, and business-rule
   statuses pass through immediately,
23. portfolio review and summary transaction windows are bounded by
   `REPORT_TRANSACTION_MAX_ROWS` and `REPORT_TRANSACTION_MAX_PAGES`; oversized windows surface
   partial transaction supportability rather than issuing unbounded lotus-core pagination calls,
24. portfolio review holdings output preserves `lotus-core` `HoldingsAsOf:v1` source-product
    metadata, source data-quality/reconciliation posture, latest evidence and lineage fields,
    position-state status, maturity date, row snapshot/evidence identity, and row source
    identifiers where sourced; partial, stale, unknown, reconciliation-incomplete, or
    trust-metadata-incomplete holdings degrade supportability instead of appearing complete,
25. portfolio review transaction output preserves `lotus-core` `TransactionLedgerWindow:v1`
    source-product metadata, source data-quality/reconciliation posture, latest evidence and
    lineage fields, settlement dates, and linked cost/cashflow evidence where sourced; partial,
    unknown, paged, or trust-metadata-incomplete windows degrade transaction supportability instead
    of appearing complete,
26. portfolio summary P&L is source-aware: unrealized P&L is sourced from lotus-core positions,
    realized P&L is sourced from transaction realized gain/loss rows, and unavailable components
    carry explicit supportability instead of synthetic market-value delta figures,
27. cross-app orchestration accuracy matters because reporting payloads summarize authoritative upstream state.
28. `GET /integration/report-ordering-catalogue` publishes the versioned Report-owned business
    catalogue for implementation-backed report families, ordering modes, formats, configuration
    fields, selectable sections, client-release posture, and live `lotus-render` supportability.
    Product-facing portfolio-review, bounded DPM, explicit-batch, and governed-schedule entry
    points reuse the same definition and validation owners so unknown families, formats, sections,
    allocation views, or configuration fields fail before durable mutation. `lotus-gateway` owns
    caller entitlement and selected-portfolio eligibility; `lotus-workbench` consumes Gateway and
    must not hard-code Report catalogue values. Report does not grant client-distribution authority.

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
   reporting evidence products. `ClientReportEvidencePack:v1` is governed for current
   `lotus-core` evidence and explicitly partial for analytics-enriched performance/risk evidence
   while `lotus-performance` and `lotus-risk` remain watchlisted.
9. `contracts/trust-telemetry/`
   repo-native RFC-0087/RFC-0091 trust telemetry snapshots for governed reporting products. The
   client-report evidence-pack snapshot must surface the analytics dependency watchlist as partial,
   quality-warning, and blocked for analytics-enriched certification until upstream approval exists.
10. `contracts/idea-evidence-intake/`
   implemented, not-certified report-owned route-foundation posture for `lotus-idea` evidence
   packet intake into `ClientReportEvidencePack`; this directory must not be treated as report
   job creation, materialization, render, archive, client-publication authority, or
   supported-feature proof. The route uses the durable idea-evidence intake ledger for
   restart-safe idempotency conflict semantics.
11. `contracts/idea-evidence-materialization/`
   implemented, not-certified report-owned materialization posture for `lotus-idea` evidence
   packets; this directory proves report-job, render, archive lifecycle wiring, and the typed
   source-safe materialization receipt only, and must keep client publication, advisory
   suitability, mandate approval, execution, distribution, and supported-feature promotion blocked.
12. `src/app/reporting_jobs/`
   shared report-job lifecycle policy, PostgreSQL runtime ledger, and an isolated SQLite unit-test
   adapter for report request/job/status lifecycle, idempotency, request hashing, status retrieval,
   bounded cancellation, versioned support-safe status-event contracts, durable source/derived
   report-job relationships, recent rerender attempt history for diagnostics, and report-owned
   portfolio-memory source events for the first asynchronous reporting wave. Replay, regenerate,
   rerender, render/archive, and batch replay
   lineage logic must consume typed event payload fields or `report_job_relationship` rows rather
   than parsing human-readable event messages or idempotency-key prefixes.
13. `src/app/reporting_lineage/`
   PostgreSQL runtime store plus an isolated SQLite unit-test adapter for durable report input
   snapshots, canonical snapshot hashing, immutable per-job capture, append-only upstream-call
   lineage, support-safe evidence query models, and readiness checks for RFC-0101. Portfolio-review
   snapshot capture depends on an injected input-provider port; concrete core/performance/risk
   client construction belongs to the provider adapter, not the capture workflow.
14. `src/app/reporting_render/`
    render-package composition, lotus-render orchestration, and `lotus-archive` handoff for
    PDF-capable report jobs.
    `package_builder.py` owns the source-backed portfolio-review render package contract, while
    `service.py` owns job lifecycle orchestration, render submission, persisted render metadata,
    archive handoff, and render/archive failure mapping.
15. `src/app/report_batch_orchestrator/`
    RFC-0104 batch reporting orchestration boundary. Shared lifecycle policy owns retry/failure
    outcome and terminal batch status reconciliation; adapters own persistence and transaction
    safety. Current slices own source-backed selector
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
16. `src/app/reporting_persistence/`
    shared forward-only PostgreSQL migration execution, supported legacy-schema classification,
    stable migration failure vocabulary, and the single internal schema-lifecycle owner consumed
    by the report-job, batch, and lineage stores. This is design modularity inside `lotus-report`,
    not a separately deployed migration service.
17. `src/app/report_ordering_catalogue/`
    immutable business report-family definitions, typed product catalogue models, live Render
    supportability composition, and shared fail-closed ordering-selection policy. Add new report
    choices here only when their submission, data package, render/archive posture, and tests are
    implementation-backed; do not create consumer-local catalogue constants.

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
5. advisor-only review material must remain separated from client-ready report sections,
6. production-like direct service access must not rely on permissive authz defaults; configure
   `ENTERPRISE_ENFORCE_AUTHZ=true`, `ENTERPRISE_ENFORCE_READ_AUTHZ=true`, and
   `ENTERPRISE_PRIMARY_KEY_ID` outside explicit local debugging.

## Repo-Native Commands

Use these commands as the primary local contract:

1. install
   `make install`
2. fast local gate
   `make check`
3. PR-grade automation gate against a caller-owned isolated PostgreSQL database
   `make ci`
4. PR-grade workstation gate with helper-owned temporary database lifecycle
   `make ci-local`
5. Docker build
   `make docker-build`
6. domain-data-product contract validation
   `make domain-product-validate`
7. idea evidence intake contract validation
   `make idea-evidence-intake-contract-gate`
8. supported prior-schema upgrade proof
   `make migration-upgrade-smoke`

## Validation And CI Expectations

`lotus-report` uses explicit CI lanes:

1. `Remote Feature Lane`
2. `Pull Request Merge Gate`
3. `Main Releasability Gate`

Important validation expectations:

1. OpenAPI, typecheck, migration smoke, and security audit are active,
2. migration smoke and CI integration proof use PostgreSQL through
   `REPORT_JOB_LEDGER_DATABASE_URL`; file databases are not runtime evidence for RFC-0100,
3. workstation PR-grade proof must use `make ci-local`; it creates one uniquely named database on
   the configured server, runs `make ci` against that database, and drops only the helper-owned
   database in guaranteed cleanup. Direct `make ci` callers must already own an isolated database
   and must never target a database used by running Report services,
4. migration smoke must prove both the current schema and the supported immediately preceding
   status-event schema through the shared production migration owner; fresh-database proof alone
   cannot clear an upgrade claim,
5. PostgreSQL migration and integration proof should promote `ResourceWarning` and
   `pytest.PytestUnraisableExceptionWarning` to errors so directly owned adapters cannot regress to
   garbage-collection cleanup,
6. RFC-0101 snapshot storage uses the same governed PostgreSQL runtime database and extends
   migration smoke with `report_input_snapshot` and `report_upstream_call` table, index, and
   check-constraint proof,
7. split unit, integration, e2e, and coverage validation are part of the merge gate,
8. reporting orchestration changes should be evaluated for cross-app impact,
9. README and wiki changes should preserve truthful explanation of API request conventions,
   especially that the first-class portfolio review endpoint publishes snake_case request, query,
   and response fields only,
10. when a remaining public surface exposes mixed query or request-body conventions, wiki or
   onboarding docs should include at least one executable request example so operators and future
   agents do not normalize the wrong parameter shape by accident,
11. PR auto-merge must use GitHub rebase auto-merge to preserve the repo's linear non-squash history
   policy; `tests/unit/test_pr_auto_merge_workflow.py` protects this workflow posture.

## Codebase Review And Issue Discovery

Use these repo-local review artifacts for governed implementation review:

1. `docs/architecture/CODEBASE-REVIEW-PLAYBOOK.md`
   canonical methodology for review units, evidence requirements, GitHub issue-discovery steps,
   duplicate handling, required issue fields, and sign-off standards,
2. `docs/architecture/CODEBASE-REVIEW-LEDGER.md`
   historical review evidence and implementation closure manifest; active backlog state must link
   GitHub issues instead of existing only in this local ledger,
3. [GitHub issue #109](https://github.com/sgajbi/lotus-report/issues/109)
   current enterprise refactor issue-discovery ledger for this campaign.

Before filing a review finding or marking one fixed locally:

1. search all GitHub issues with affected file names, lens labels, and failure-pattern terms,
2. reuse duplicate issues when the root cause and acceptance criteria already match,
3. create or reuse one high-value issue per validated finding or coherent finding cluster,
4. include evidence, expected direction, acceptance criteria, duplicate-search proof, and
   validation proof in the issue,
5. update #109 with the issue number, lens, duplicate-search proof, and status,
6. link the issue from local ledger rows only when the ledger records accepted work or closure
   evidence.

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
   approve `lotus-report` as a governed consumer. Do not publish complete, quality-passed,
   unblocked `ClientReportEvidencePack` trust telemetry for analytics-enriched evidence while
   those dependencies remain watchlisted.

## Context Maintenance Rule

Update this document when:

1. report payload ownership or major orchestration scope changes,
2. repo-native commands or CI expectations change,
3. upstream dependency posture changes materially,
4. canonical runtime identity or front-office integration role changes,
5. current request-convention compatibility or canonical parameter naming changes,
6. durable reporting job lifecycle, typed status-event contract, idempotency, or ledger persistence
   posture changes,
7. durable report input snapshot or upstream-call lineage persistence, hashing, readiness, API, or
   migration posture changes,
8. render-package composition, lotus-render integration, persisted render metadata, or job
   lifecycle semantics change,
9. report ledger database, readiness, migration, or CI proof posture changes,
10. current-state rollout posture changes,
11. RFC-0104 batch orchestration module, selector materialization, support posture, or
    planned-vocabulary scope changes,
12. RFC-0105 observability, metrics, dashboard, alert, operator API, replay, rerender, or
    regenerate support posture changes,
13. codebase review methodology, issue-discovery workflow, review ledger semantics, or GitHub issue
    lifecycle expectations change,
14. planned or implemented `lotus-idea` evidence-pack intake posture, source-authority boundaries,
    route/materialization proof, or supported-feature promotion changes.

## Cross-Links

1. `../lotus-platform/context/LOTUS-QUICKSTART-CONTEXT.md`
2. `../lotus-platform/context/LOTUS-ENGINEERING-CONTEXT.md`
3. `../lotus-platform/context/CONTEXT-REFERENCE-MAP.md`
4. `../lotus-platform/context/Repository-Engineering-Context-Contract.md`
5. [Lotus Developer Onboarding](../lotus-platform/docs/onboarding/LOTUS-DEVELOPER-ONBOARDING.md)
6. [Lotus Agent Ramp-Up](../lotus-platform/docs/onboarding/LOTUS-AGENT-RAMP-UP.md)
7. [Codebase Review Playbook](docs/architecture/CODEBASE-REVIEW-PLAYBOOK.md)
8. [Codebase Review Ledger](docs/architecture/CODEBASE-REVIEW-LEDGER.md)
