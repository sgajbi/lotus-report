# lotus-report

Reporting and aggregation service for Lotus portfolio summary and portfolio review payloads.

Repository-local engineering context:
[REPOSITORY-ENGINEERING-CONTEXT.md](REPOSITORY-ENGINEERING-CONTEXT.md)

Local ownership guidance:
[docs/standards/data-model-ownership.md](docs/standards/data-model-ownership.md)

## Purpose And Scope

`lotus-report` owns reporting-oriented composition:

- reporting read-model aggregation
- portfolio summary payload shaping
- first-class portfolio review report payload shaping for client/advisor meetings
- report metadata and download-reference contracts
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
- `lotus-gateway`
  primary product-facing consumer for front-office reporting workflows

Boundary rules that matter:

1. upstream domain truth stays in the authoritative services
2. `lotus-report` owns reporting aggregation and response shape, not ledger or analytics authority
3. cross-app reporting payloads must stay faithful to upstream evidence
4. canonical service identity for cross-app validation is `http://report.dev.lotus`

## Current Operational Posture

1. `lotus-report` composes portfolio summary and review payloads from `lotus-core`,
   `lotus-performance`, and `lotus-risk`.
2. It is part of the canonical front-office stack and is exposed through `report.dev.lotus`.
3. `POST /reports/portfolios/{portfolio_id}/review` is the RFC-0002 first-class portfolio review
   report contract with typed request/response models, normalized machine-readable JSON section
   items, explicit section readiness, evidence lineage, and advisor-only discussion sections.
4. CI is standardized under the Lotus lane model, though lighter than some domain-authoritative
   services.
5. Request conventions are mixed by surface: integration capabilities use snake_case query
   parameters, while several reporting and aggregation request shapes still expose camelCase aliases
   such as `asOfDate` and `sectionLimit`.

## Architecture At A Glance

Main runtime surfaces come from [src/app/main.py](src/app/main.py):

- integration capabilities
  `GET /integration/capabilities`
- aggregations
  `GET /aggregations/portfolios/{portfolio_id}`
- report generation
  `POST /reports`
- reporting read endpoints
  `POST /reports/portfolios/{portfolio_id}/summary`
  `POST /reports/portfolios/{portfolio_id}/review`
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
- `src/app/clients/`
  lotus-core, lotus-performance, lotus-risk, and HTTP resilience clients
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
$env:PYTHONPATH="src"
uvicorn app.main:app --reload --port 8300
```

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
  security audit
- `make ci-local`
  local alias for the repo’s PR-grade gate
- `make docker-build`
  container build validation

## API Contract Notes

Important current request conventions:

1. `GET /integration/capabilities` expects canonical snake_case query parameters
   `consumer_system` and `tenant_id`
2. `GET /aggregations/portfolios/{portfolio_id}` currently uses camelCase query alias `asOfDate`
3. `POST /reports/portfolios/{portfolio_id}/summary` and `/review` use camelCase query alias
   `sectionLimit`
4. reporting read request bodies currently accept both `as_of_date` and `asOfDate` compatibility
   forms in service logic

Keep those differences explicit in documentation until the repo intentionally standardizes them.

Copy-paste request examples live in [wiki/API-Surface.md](wiki/API-Surface.md).

## Upstream Defaults

Cross-app upstream defaults in local runtime:

- `LOTUS_CORE_QUERY_BASE_URL=http://core-query.dev.lotus`
- `LOTUS_PERFORMANCE_BASE_URL=http://performance.dev.lotus`
- `RISK_BASE_URL=http://risk.dev.lotus`

When `lotus-report` runs in Docker Compose as part of the canonical front-office stack, the
container uses host-reachable upstream URLs instead:

- `LOTUS_CORE_QUERY_BASE_URL=http://host.docker.internal:8201`
- `LOTUS_PERFORMANCE_BASE_URL=http://host.docker.internal:8002`
- `RISK_BASE_URL=http://host.docker.internal:8130`

This keeps `report.dev.lotus` stable for callers while allowing the containerized report service to
reach the host-published canonical upstream ports.

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

## Documentation Map

- local ownership guidance:
  [docs/standards/data-model-ownership.md](docs/standards/data-model-ownership.md)
- local operations workflow:
  [docs/operations/development-workflow-and-ci-strategy.md](docs/operations/development-workflow-and-ci-strategy.md)
- supported features:
  [docs/supported-features.md](docs/supported-features.md)
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
