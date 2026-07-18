# lotus-report

Reporting and aggregation service for Lotus portfolio summary, portfolio review, and evidence
payloads.

Repository-local engineering context:
[REPOSITORY-ENGINEERING-CONTEXT.md](REPOSITORY-ENGINEERING-CONTEXT.md)

Local ownership guidance:
[docs/standards/data-model-ownership.md](docs/standards/data-model-ownership.md)

## Purpose And Scope

`lotus-report` owns reporting-oriented composition:

- reporting read-model aggregation
- portfolio summary payload shaping
- portfolio review report payload shaping for client/advisor meetings
- PostgreSQL-backed durable portfolio review report job ledger for gateway-first initiation and
  operational search, status tracking, event history, and bounded cancellation
- reporting capability publication for downstream consumers

It does not own core portfolio data, performance truth, or risk methodology. Those remain in the
authoritative upstream services.

## Ownership And Boundaries

`lotus-report` is an orchestration-heavy reporting service.

It depends on:

- `lotus-core`
  portfolio summary, asset allocation, positions, and transaction source-data contracts
- `lotus-performance`
  workspace summary and performance analytics inputs used for reporting views
- `lotus-risk`
  risk analytics derived from reporting review flows
- `lotus-render`
  deterministic PDF rendering for governed report packages
- `lotus-archive`
  rendered document archival after successful PDF render completion
- `lotus-gateway`
  primary product-facing consumer for front-office reporting workflows
- `lotus-idea`
  approved producer of reviewed opportunity evidence packets for report evidence-pack intake;
  `POST /reports/idea-evidence-packs` proves the route foundation only, while report
  materialization, render, archive, client-publication authority, and supported-feature promotion
  remain not certified

Boundary rules that matter:

1. upstream domain truth stays in the authoritative services
2. `lotus-report` owns reporting aggregation, response shape, and reporting job lifecycle state, not
   portfolio, performance, or risk analytics authority
3. cross-app reporting payloads must stay faithful to upstream evidence
4. canonical service identity for cross-app validation is `http://report.dev.lotus`

## Current Operational Posture

1. `lotus-report` composes portfolio summary and review payloads from `lotus-core`,
   `lotus-performance`, and `lotus-risk`.
2. It is part of the canonical front-office stack and is exposed through `report.dev.lotus`.
3. `POST /reports/portfolios/{portfolio_id}/review` is the portfolio review report contract with
   typed request/response models, normalized machine-readable JSON,
   client/advisor section separation, explicit section readiness, evidence lineage, source-backed
   key figures, deterministic advisor briefing, report-structure guidance, and guarded AI-readiness
   metadata.
4. `POST /reports/portfolio-reviews`, `POST /reports/outcome-reviews`,
   `POST /reports/proof-packs`, `POST /reports/rebalance-waves`, `GET /reports/jobs`,
   `GET /reports/jobs/{job_id}`, `GET /reports/jobs/{job_id}/events`, and
   `POST /reports/jobs/{job_id}/cancel` provide the
   durable job-ledger foundation for gateway-first report initiation, operator-safe job search,
   product-safe status, append-only event history, database-backed idempotency, immutable snapshot
   and lineage capture, lotus-render submission for PDF output, `lotus-archive` handoff after
   successful render completion, and bounded cancellation before the job reaches `rendering`.
   Outcome-review, proof-pack, and rebalance-wave job routes consume manage-owned bounded report
   inputs and do not recompute DPM evidence. Archive retrieval, retention execution, legal hold,
   purge, and document distribution remain owned by `lotus-archive`.
5. CI is standardized under the Lotus lane model, though lighter than some domain-authoritative
   services.
6. Request conventions are governed by the Lotus API vocabulary standard. Public query,
   request-body, and response fields use canonical snake_case names and do not publish camelCase
   compatibility aliases.
7. `contracts/idea-evidence-intake/lotus-report-idea-evidence-pack-intake.v1.json` records the
   implemented, not-certified `lotus-idea` evidence-pack intake route boundary for
   `ClientReportEvidencePack`. It proves only source-safe route intake through
   `POST /reports/idea-evidence-packs`; it is not report materialization, render, archive,
   client-publication authority, or supported-feature proof. Intake idempotency is persisted in a
   SQLite ledger configured by `IDEA_EVIDENCE_INTAKE_LEDGER_PATH`; records store support-safe
   fingerprints, source identifiers, caller context, correlation id, and trace id, not raw evidence
   payloads. Report resolves `retention_policy_ref` against the versioned policy contract before
   intake or materialization; unknown, inactive, unauthorized, and tenant-mismatched references
   fail before persistence, while active legal holds propagate to the report job and Archive
   handoff metadata.

## First-Class Portfolio Review

The portfolio review endpoint is the main front-office reporting capability in this repository. It
is designed for client advisor review meetings where the output must be useful to a human advisor,
machine-readable for Workbench/gateway consumers, and honest about what is sourced versus not
sourced.

The response includes:

- `client_profile`
  source-backed client, advisor, booking-center, mandate, objective, risk exposure, horizon,
  leverage, status, and cost-basis context from `lotus-core`
- `key_figures`
  normalized portfolio value, allocation, performance, risk, income/activity, holdings,
  unrealized P&L, transaction-level realized P&L, position contribution, and client-profile figures
- `client_sections`
  ordered client-ready report sections with explicit readiness states
- `advisor_sections`
  advisor-only deterministic prompts and route targets that must not be rendered as client report
  content without an explicit product decision
- `report_coverage`, `upstream_capability_audit`, and `review_observations`
  sourced, partial, and missing coverage so unsupported gold-standard material is visible instead
  of silently omitted
- `report_structure` and `advisor_briefing`
  deterministic meeting-pack organization and advisor-useful talking points
- `ai_readiness`
  guarded metadata for grounded AI assistance, with trade recommendations, suitability
  determinations, and inferred client profiles explicitly blocked
- `evidence`
  lineage and trust metadata for downstream governance and auditability

Current live-proof portfolio:

- governed portfolio id: `PB_SG_GLOBAL_BAL_001`
- governed local endpoint:
  `POST http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20`
- full example and operating guidance:
  [wiki/Portfolio-Review-Report.md](wiki/Portfolio-Review-Report.md)

Important limitation: `lotus-report` does not invent suitability, target allocation, product
restriction, liquidity-need, open tax-lot attribution, or jurisdiction-specific tax treatment.
Transaction-level realized gain/loss is sourced from `lotus-core` transaction rows where present;
tax-lot and jurisdiction-specific reporting must come from the authoritative upstream owner before
they become report-backed product features.

## Architecture At A Glance

Main runtime surfaces come from [src/app/main.py](src/app/main.py):

- integration capabilities
  `GET /integration/capabilities`
- report ordering catalogue
  `GET /integration/report-ordering-catalogue`
- aggregations
  `GET /aggregations/portfolios/{portfolio_id}`
- reporting read endpoints
  `POST /reports/portfolios/{portfolio_id}/summary`
  `POST /reports/portfolios/{portfolio_id}/review`
- report job lifecycle endpoints
  `POST /reports/portfolio-reviews`
  `GET /reports/jobs`
  `GET /reports/jobs/{job_id}`
  `GET /reports/jobs/{job_id}/events`
  `POST /reports/jobs/{job_id}/cancel`
- platform surfaces
  `/health`, `/health/live`, `/health/ready`, `/metrics`, `/docs`

Key code areas:

- `src/app/routers/`
  FastAPI route surfaces for health, integration, aggregations, and reports
- `src/app/services/reporting_read_service.py`
  upstream composition for summary and review payloads
- `src/app/services/portfolio_review_advisor.py`
  deterministic advisor-only discussion prompts and route targets for review meetings
- `src/app/services/aggregation_service.py`
  aggregation read-model composition and live/static aggregation flows
- `src/app/reporting_jobs/`
  durable report request/job/status-event ledger, idempotency, render metadata, archive metadata,
  rerender attempt state, and bounded cancellation
- `src/app/reporting_render/`
  governed render-package assembly, lotus-render orchestration, post-render archive handoff, and
  archived-report rerender from immutable snapshot, upstream regeneration, and failed-work replay
- `src/app/reporting_metrics.py`
  RFC-0105 first-wave Prometheus metric vocabulary for implemented report job, snapshot, render,
  archive, rerender-from-snapshot, regenerate-from-upstream, failed-work replay command, batch
  worker, scheduler, and source-backed operations attention scan behavior, with reserved dedicated
  broader replay posture and high-cardinality label rejection
- `src/app/report_batch_orchestrator/`
  RFC-0104 batch reporting module boundary, planned vocabulary, and internal durable
  batch/batch-item, deterministic schedule-cycle, dispatch, lease, back-pressure, bounded retry,
  pause/resume, cancellation-boundary, expired-lease recovery primitives, and certified
  materialization/status/control APIs. Internal item execution can reuse the existing report-job,
  snapshot, render, and archive handoff path, and an internal bounded worker run primitive can
  combine recovery, dispatch, and waiting-item execution for one batch. `POST
  /reports/batches/{batch_id}:run-once` exposes that bounded worker pass as an internal
  operator-controlled API. A bounded internal runtime pass can scan durable runnable batches and
  invoke that worker primitive for a limited number of batches. The
  `lotus-report-batch-worker` Docker Compose service runs that pass as a daemonized internal
  background worker process. The `lotus-report-batch-scheduler` Docker Compose service reads
  governed `REPORT_BATCH_SCHEDULES_JSON`, resolves explicit, all-active, and inline manifest
  schedule selectors through `lotus-core` or governed schedule manifest metadata, and creates
  durable idempotent scheduled batches for the worker to execute. `GET /reports/batch-schedules`
  and `POST /reports/batch-schedules:run-due` expose config-backed scheduler inspection and a
  bounded scheduler materialization pass; schedule CRUD and entitlement-certified public scheduler
  runtime remain future scope
- `src/app/report_ordering_catalogue/`
  Report-owned, versioned business catalogue for supported report families, ordering modes,
  formats, configuration fields, sections, and live Render supportability. The same definitions
  validate product-facing single-portfolio, batch, source-workflow, and governed-schedule choices
  before durable mutation. Gateway owns entitlement and selected-scope eligibility; Workbench must
  consume the Gateway projection rather than hard-code report choices.
- `src/app/clients/`
  lotus-core, lotus-performance, lotus-risk, lotus-render, and HTTP resilience clients
- `docs/standards/`
  ownership, readiness, migration, precision, and scalability guidance
- `docs/supported-features.md`
  implementation-backed product capability registry

## Repository Layout

- `src/app/main.py`
  FastAPI entrypoint and router registration
- `src/app/routers/`
  public HTTP surfaces for health, integration, aggregations, and reports
- `src/app/services/`
  reporting composition and aggregation orchestration logic
- `src/app/clients/`
  upstream lotus-core, lotus-performance, and lotus-risk clients
- `tests/`
  unit, integration, and e2e coverage for reporting behavior
- `scripts/`
  OpenAPI, migration, and monetary-float governance checks
- `docs/supported-features.md`
  implementation-backed product capability registry
- `wiki/`
  canonical authored source for the GitHub wiki page set

## Quick Start

Install dependencies:

```bash
make install
```

Run the service locally:

```powershell
$env:ENTERPRISE_RUNTIME_PROFILE="local"
$env:REPORT_JOB_LEDGER_DATABASE_URL="postgresql://lotus_report:lotus_report@localhost:5439/lotus_report"
$env:PYTHONPATH="src"
uvicorn app.main:app --reload --port 8300
```

For local runtime parity, start a PostgreSQL database before launching the process. The repository
Docker Compose file provides `lotus-report-postgres` on host port `5439`;
`REPORT_JOB_LEDGER_DATABASE_URL` must point to PostgreSQL for runtime, integration, migration, and
live-evidence proof.

Existing supported Report volumes are upgraded in place before the API, batch worker, or scheduler
starts. Validate both current-schema and prior-schema compatibility with `make migration-smoke`.
Use `make migration-upgrade-smoke` when you need only the isolated prior-schema proof. Do not delete
the volume as the default response to a startup failure: preserve it and capture the stable
`lotus_report_schema_startup_failed` diagnostic described in
`docs/standards/migration-contract.md`.

Canonical local service identity:

- cross-app validation: `http://report.dev.lotus`
- direct process debugging: `http://127.0.0.1:8300`

Quick health probes:

```bash
curl http://127.0.0.1:8300/health
curl "http://127.0.0.1:8300/integration/capabilities?consumer_system=lotus-gateway&tenant_id=default"
```

## Common Commands

- `make install`
  install dependencies and pre-commit hooks
- `make check`
  fast local gate: lint, typecheck, OpenAPI gate, and unit tests
- `make ci`
  PR-grade automation proof against a caller-owned isolated PostgreSQL database: migration smoke,
  integration, e2e, coverage, and security audit
- `make migration-upgrade-smoke`
  real PostgreSQL proof that the supported pre-contract status-event schema upgrades in place,
  preserves the legacy row, and can safely rerun without touching the public schema
- `make ci-local`
  safe workstation proof that creates one temporary database, runs the repo CI contract, and drops
  only that database on success or failure
- `make docker-build`
  container build validation

## Validation And CI Lanes

`lotus-report` follows the Lotus multi-lane model:

1. `Remote Feature Lane`
2. `Pull Request Merge Gate`
3. `Main Releasability Gate`

Repo-native gate mapping:

- `make check`
  lint, typecheck, OpenAPI gate, and unit tests
- `make ci`
  merge-gate automation proof with migration smoke, integration tests, e2e tests, coverage, and
  security audit. The caller must provide an isolated PostgreSQL database through
  `REPORT_JOB_LEDGER_DATABASE_URL`; never point this target at a database used by running services.
- `make ci-local`
  preferred workstation command. It uses the configured PostgreSQL server and credentials to
  create a uniquely named database, runs `make ci` against that database, and drops only the
  helper-owned database in a guaranteed cleanup path.
- `make docker-build`
  container build validation

For safe PR-grade proof while the canonical Report stack remains running:

```powershell
$env:REPORT_JOB_LEDGER_DATABASE_URL="postgresql://lotus_report:lotus_report@localhost:5439/lotus_report"
make ci-local
```

The configured PostgreSQL role must be allowed to create and drop databases. The source database
name is used only to derive a bounded temporary name; `ci-local` does not run migrations or tests
against the source database, does not delete the canonical volume, and does not print the DSN.

## API Contract Notes

Important current request conventions:

1. `GET /integration/capabilities` expects canonical snake_case query parameters
   `consumer_system` and `tenant_id`
2. `GET /aggregations/portfolios/{portfolio_id}` expects canonical snake_case query parameter
   `as_of_date`
3. `POST /reports/portfolios/{portfolio_id}/summary` and `/review` use canonical snake_case
   `section_limit`
4. `POST /reports/portfolios/{portfolio_id}/review` publishes snake_case request and response
   fields only; camelCase aliases are rejected by the typed request model

Do not add compatibility aliases for alternate case styles. If a public field name needs to
change, treat it as a governed contract change with tests, OpenAPI updates, and downstream
coordination.

Copy-paste request examples live in [wiki/API-Surface.md](wiki/API-Surface.md).
The portfolio review contract is explained in
[wiki/Portfolio-Review-Report.md](wiki/Portfolio-Review-Report.md).

## Upstream Defaults

Cross-app upstream defaults in local runtime:

- `LOTUS_CORE_QUERY_BASE_URL=http://core-query.dev.lotus`
- `LOTUS_PERFORMANCE_BASE_URL=http://performance.dev.lotus`
- `RISK_BASE_URL=http://risk.dev.lotus`
- `LOTUS_ARCHIVE_BASE_URL=http://archive.dev.lotus`
- `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report`
- `REPORT_POSTGRES_POOL_MIN_SIZE=1`
- `REPORT_POSTGRES_POOL_MAX_SIZE=10`
- `REPORT_POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS=5`
- `REPORT_POSTGRES_CONNECT_TIMEOUT_SECONDS=5`
- `REPORT_POSTGRES_STATEMENT_TIMEOUT_MS=30000`
- `REPORT_POSTGRES_APPLICATION_NAME=lotus-report`

When `lotus-report` runs in Docker Compose as part of the canonical front-office stack, the
container uses host-reachable upstream URLs instead:

- `LOTUS_CORE_QUERY_BASE_URL=http://host.docker.internal:8201`
- `LOTUS_PERFORMANCE_BASE_URL=http://host.docker.internal:8002`
- `RISK_BASE_URL=http://host.docker.internal:8130`
- `LOTUS_ARCHIVE_BASE_URL=http://host.docker.internal:8150`
- `REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@lotus-report-postgres:5432/lotus_report`

This keeps `report.dev.lotus` stable for callers while allowing the containerized report service to
reach the host-published canonical upstream ports. The report job ledger uses the separate
`lotus-report-postgres` container; file databases are not valid runtime evidence.

Current orchestration model:

1. summary/reporting views use lotus-core portfolio summary, asset allocation, positions, and
   transaction contracts
2. review performance uses `POST /performance/workspace-summary` in stateful mode
3. review risk analytics derive from the resulting daily return stream and are then forwarded into
   lotus-risk

## Integration Boundaries

- primary downstream consumer:
  `lotus-gateway` for front-office reporting workflows
- upstream evidence producer:
  `lotus-idea` for reviewed opportunity evidence packets through the implemented
  `POST /reports/idea-evidence-packs` route foundation; materialization, render, archive, and
  client-publication proof remain separate certification work
- upstream dependencies:
  `lotus-core`, `lotus-performance`, `lotus-risk`
- contract rule:
  reporting payloads may reshape and aggregate upstream data, but they must not reinterpret domain
  ownership or invent unsupported business truth

## Operations And Runtime Posture

- use `report.dev.lotus` for canonical cross-app validation and ingress-aware checks
- use `127.0.0.1:8300` only for direct local debugging with
  `ENTERPRISE_RUNTIME_PROFILE=local`
- production-like runtime profiles (`prod`, `production`, `preprod`, `staging`, and `uat`) fail
  closed for direct service access: write and read authorization are enforced even when authz
  toggles are omitted, and startup validation fails unless `ENTERPRISE_ENFORCE_AUTHZ=true`,
  `ENTERPRISE_ENFORCE_READ_AUTHZ=true`, and `ENTERPRISE_PRIMARY_KEY_ID` are configured
- treat reporting errors as orchestration issues first: verify upstream responses and request-shape
  compatibility before changing response formatting
- preserve observability, correlation, request, and trace behavior on reporting endpoints,
  especially when debugging summary, review, batch, render, or archive flows
- use `ENTERPRISE_ENFORCE_AUTHZ=true` and `ENTERPRISE_ENFORCE_READ_AUTHZ=true` outside local
  debug to require service identity or authorization on write and `GET`/`HEAD` surfaces; use
  `ENTERPRISE_AUDIT_READS=true` to emit identifier-only read audit events through the enterprise
  readiness middleware
- treat `docs/operations/reporting-observability-metrics.md` as the current RFC-0105 metrics,
  dashboard, alert, and label-governance contract; dedicated broader replay dashboards remain
  reserved until those command paths are implementation-backed
- treat `/health/ready` as a database-aware readiness probe; it returns unavailable when the
  PostgreSQL ledger or mandatory schema is not reachable
- treat `lotus_report_schema_startup_failed:report_schema_upgrade_unsupported` as an operator
  migration/version diagnostic: preserve the database volume and use the governed upgrade or
  recovery path rather than resetting durable report history
- PostgreSQL-backed report-job, batch, and snapshot/upstream-call stores share one bounded
  process-local connection provider; tune `REPORT_POSTGRES_POOL_MAX_SIZE`,
  `REPORT_POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS`, `REPORT_POSTGRES_CONNECT_TIMEOUT_SECONDS`,
  `REPORT_POSTGRES_STATEMENT_TIMEOUT_MS`, and `REPORT_POSTGRES_APPLICATION_NAME` before raising
  worker or scheduler concurrency
- use `GET /reports/jobs/{job_id}/diagnostics` as the first RFC-0105 operator view for one report
  job; it composes source-backed status, lifecycle-event, snapshot, lineage, render, and archive
  handoff posture while omitting raw payloads, storage references, and database internals
- use `POST /reports/jobs/{job_id}/rerender` only for already archived PDF jobs when operations
  need a correction document from the same immutable snapshot; the response proves the same
  snapshot id/hash and a new rerender/render/archive identity
- use `POST /reports/jobs/{job_id}/regenerate` only for already archived PDF jobs when operations
  need to refresh upstream data and create a replacement document; the response proves the old and
  new report job, snapshot, snapshot hash, and archive document identities
- use `POST /reports/jobs/{job_id}/replay` only for failed retry-eligible report jobs; it creates
  or reuses a replay-scoped report job and rejects completed, archived, cancelled, or non-retryable
  source jobs
- portfolio review and summary transaction windows are bounded by
  `REPORT_TRANSACTION_MAX_ROWS` and `REPORT_TRANSACTION_MAX_PAGES`; oversized windows return a
  partial transaction supportability state instead of issuing unbounded lotus-core pagination calls
- use `POST /reports/batches/{batch_id}/items/{batch_item_id}/replay` only for failed
  retry-eligible implementation-backed batch items linked to failed report jobs; it relinks the
  item to replay work without scheduler CRUD, registry mutation, distribution, or archive
  housekeeping behavior
- use `GET /reports/jobs/{job_id}/events` for deeper lifecycle diagnostics before inspecting
  database rows directly
- use `POST /reports/batches` and `GET /reports/batches/{batch_id}` only for the certified
  internal batch materialization/status subset; pause, resume, cancel, retry-failed, and
  recover-expired-leases controls plus the bounded `run-once` operator action are direct
  `lotus-report` APIs and are not yet gateway or Workbench surfaces. The internal runtime pass and
  daemonized worker/scheduler processes are `lotus-report` service primitives, not public APIs

## Documentation Map

- local ownership guidance:
  [docs/standards/data-model-ownership.md](docs/standards/data-model-ownership.md)
- local operations workflow:
  [docs/operations/development-workflow-and-ci-strategy.md](docs/operations/development-workflow-and-ci-strategy.md)
- supported features:
  [docs/supported-features.md](docs/supported-features.md)
- portfolio review report guide:
  [wiki/Portfolio-Review-Report.md](wiki/Portfolio-Review-Report.md)
- local standards:
  [docs/standards](docs/standards)
- wiki home:
  [wiki/Home.md](wiki/Home.md)
- API request examples:
  [wiki/API-Surface.md](wiki/API-Surface.md)

## Wiki Source

Repository-authored wiki pages live under [wiki/](wiki). If the GitHub wiki is published later,
keep `wiki/` as the canonical source and treat any separate `*.wiki.git` clone as publication
plumbing only.
