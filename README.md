# Reporting & Aggregation Service

Repository-local engineering context: `REPOSITORY-ENGINEERING-CONTEXT.md`

Service scope:
- build aggregated read models for reporting from lotus-core and lotus-performance data contracts
- generate reporting artifacts metadata and download references
- own reporting endpoints for portfolio summary and portfolio review payloads

## Local Run

```powershell
python -m pip install -e ".[dev]"
$env:PYTHONPATH="src"
uvicorn app.main:app --reload --port 8300
```

API docs:
- canonical local service identity: `http://report.dev.lotus`
- direct process port for local-only debugging: `8300`

Cross-app upstream defaults:
- `LOTUS_CORE_QUERY_BASE_URL=http://core-query.dev.lotus`
- `LOTUS_PERFORMANCE_BASE_URL=http://performance.dev.lotus`
- `RISK_BASE_URL=http://risk.dev.lotus`

Key reporting endpoints:
- `GET /integration/capabilities`
- `POST /reports/portfolios/{portfolio_id}/summary`
- `POST /reports/portfolios/{portfolio_id}/review`

Capability discovery query contract:
- use canonical snake_case query parameters `consumer_system` and `tenant_id`

Current orchestration model:
- lotus-report composes summary/review responses from lotus-core portfolio summary, asset
  allocation, positions, and transaction contracts.
- lotus-report derives review performance and risk-ready return series from
  `POST /performance/workspace-summary` in stateful mode and forwards the resulting daily return
  stream into lotus-risk for risk analytics.

## Tests

```powershell
$env:PYTHONPATH="src"
python -m pytest tests -q
```

## Docker

```powershell
docker compose up -d --build
```

Canonical local service identity:

- `http://report.dev.lotus`

For the shared front-office stack, `lotus-report` stays Docker-backed on port `8300` and is exposed
through direct ingress as `report.dev.lotus`. Use the canonical URL for all cross-app probing and
validation rather than the raw port.

## Platform Foundation Commands

- `make migration-smoke`
- `make migration-apply`
- `make security-audit`

Standards documentation:

- `docs/standards/migration-contract.md`
- `docs/standards/data-model-ownership.md`
