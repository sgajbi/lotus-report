# Operations Runbook

## Important operational checks

- confirm canonical reporting identity is `report.dev.lotus` before cross-app validation
- treat upstream client failures as reporting-orchestration issues first, not as local formatting bugs
- verify correlation and observability headers on reporting endpoints
- use repo-native gates before inventing ad hoc checks

## Health and readiness surfaces

- `/health`
  broad service-health probe
- `/health/live`
  liveness probe
- `/health/ready`
  readiness probe for traffic acceptance
- `/metrics`
  observability surface for runtime monitoring

## Operational truths

- `lotus-report` composes from lotus-core, lotus-performance, and lotus-risk
- reporting payload quality depends on upstream fidelity and contract handling
- direct process port `8300` is useful for local debugging, but canonical cross-app validation
  should use `report.dev.lotus`
- Docker Compose uses `host.docker.internal` upstream URLs so the container can reach the
  host-published canonical upstream ports while callers continue to use `report.dev.lotus`

## Practical probes

```powershell
curl http://127.0.0.1:8300/health/ready
curl "http://127.0.0.1:8300/aggregations/portfolios/DEMO_DPM_EUR_001?asOfDate=2026-02-24&live=false"
```

## Key references

- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/standards/enterprise-readiness.md](../docs/standards/enterprise-readiness.md)
- [docs/standards/migration-contract.md](../docs/standards/migration-contract.md)
- [docs/standards/scalability-availability.md](../docs/standards/scalability-availability.md)
