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
4. `POST /reports/portfolio-reviews`, `GET /reports/jobs`, `GET /reports/jobs/{job_id}`,
   `GET /reports/jobs/{job_id}/events`, and `POST /reports/jobs/{job_id}/cancel` provide the
   durable job-ledger foundation for gateway-first report initiation, operator-safe job search,
   product-safe status, append-only event history, database-backed idempotency, immutable snapshot
   and lineage capture, lotus-render submission for PDF output, `lotus-archive` handoff after
   successful render completion, and bounded cancellation before the job reaches `rendering`.
   Archive retrieval, retention execution, legal hold, purge, and document distribution remain owned
   by `lotus-archive`.
5. CI is standardized under the Lotus lane model, though lighter than some domain-authoritative
   services.
6. Request conventions are governed by the Lotus API vocabulary standard. Public query,
   request-body, and response fields use canonical snake_case names and do not publish camelCase
   compatibility aliases.

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
  and bounded cancellation
- `src/app/reporting_render/`
  governed render-package assembly, lotus-render orchestration, and post-render archive handoff
- `src/app/report_batch_orchestrator/`
  RFC-0104 batch reporting module boundary, planned vocabulary, and internal durable
  batch/batch-item, deterministic schedule-cycle, dispatch, lease, back-pressure, bounded retry,
  pause/resume, cancellation-boundary, expired-lease recovery primitives, and certified
  materialization/status/control APIs. Internal item execution can reuse the existing report-job,
  snapshot, render, and archive handoff path; no batch scheduler loop, worker process, dispatch
  operator API, gateway exposure, or Workbench batch surface is implemented yet
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

```bash
$env:REPORT_JOB_LEDGER_DATABASE_URL="postgresql://lotus_report:lotus_report@localhost:5439/lotus_report"
$env:PYTHONPATH="src"
uvicorn app.main:app --reload --port 8300
```

For local runtime parity, start a PostgreSQL database before launching the process. The repository
Docker Compose file provides `lotus-report-postgres` on host port `5439`;
`REPORT_JOB_LEDGER_DATABASE_URL` must point to PostgreSQL for runtime, integration, migration, and
live-evidence proof.

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
  PR-grade local proof: migration smoke, integration, e2e, coverage, and security audit
- `make ci-local`
  local alias for the repo CI contract
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
  merge-gate local proof with migration smoke, integration tests, e2e tests, coverage, and
  security audit. `REPORT_JOB_LEDGER_DATABASE_URL` must be set to a reachable PostgreSQL database
  for migration smoke and Postgres ledger integration proof.
- `make ci-local`
  local alias for the repo’s PR-grade gate
- `make docker-build`
  container build validation

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
- upstream dependencies:
  `lotus-core`, `lotus-performance`, `lotus-risk`
- contract rule:
  reporting payloads may reshape and aggregate upstream data, but they must not reinterpret domain
  ownership or invent unsupported business truth

## Operations And Runtime Posture

- use `report.dev.lotus` for canonical cross-app validation and ingress-aware checks
- use `127.0.0.1:8300` only for direct local debugging
- treat reporting errors as orchestration issues first: verify upstream responses and request-shape
  compatibility before changing response formatting
- preserve observability and correlation behavior on reporting endpoints, especially when debugging
  summary or review flows
- treat `/health/ready` as a database-aware readiness probe; it returns unavailable when the
  PostgreSQL ledger or mandatory schema is not reachable
- use `GET /reports/jobs/{job_id}/events` for support-facing lifecycle diagnostics before
  inspecting database rows directly
- use `POST /reports/batches` and `GET /reports/batches/{batch_id}` only for the certified
  internal batch materialization/status subset; pause, resume, cancel, retry-failed, and
  recover-expired-leases controls are direct `lotus-report` APIs and are not yet gateway or
  Workbench surfaces

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
