# Operations Runbook

## Important operational checks

- confirm canonical reporting identity is `report.dev.lotus` before cross-app validation
- treat upstream client failures as reporting-orchestration issues first, not as local formatting bugs
- verify correlation and observability headers on reporting endpoints
- use repo-native gates before inventing ad hoc checks
- for portfolio review evidence, verify the JSON contract, section readiness, report coverage,
  advisor-only separation, AI-readiness guardrails, and evidence lineage before treating the output
  as meeting-ready

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
curl "http://127.0.0.1:8300/aggregations/portfolios/DEMO_DPM_EUR_001?as_of_date=2026-02-24&live=false"
```

Portfolio review proof:

```powershell
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20" `
  -H "Content-Type: application/json" `
  -H "X-Correlation-ID: portfolio-review-local-proof" `
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

Expected posture:

1. `client_profile.status` should show sourced profile state or explicit missing fields.
2. `key_figures` should include portfolio, allocation, performance, contribution, risk,
   holdings/P&L, activity, and client-profile families where sourced.
3. `report_coverage` should mark unsupported enterprise-grade families as `not_sourced` instead of
   silently omitting them.
4. `advisor_briefing` should stay deterministic and advisor-only.
5. `ai_readiness` should describe guarded assistance and blocked advice/suitability use cases.

## Key references

- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/standards/enterprise-readiness.md](../docs/standards/enterprise-readiness.md)
- [docs/standards/migration-contract.md](../docs/standards/migration-contract.md)
- [docs/standards/scalability-availability.md](../docs/standards/scalability-availability.md)
- [Portfolio Review Report](Portfolio-Review-Report)
