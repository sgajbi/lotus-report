# Operations Runbook

## Important operational checks

- confirm canonical reporting identity is `report.dev.lotus` before cross-app validation
- treat upstream client failures as reporting-orchestration issues first, not as local formatting bugs
- verify correlation and observability headers on reporting endpoints
- use repo-native gates before inventing ad hoc checks
- for portfolio review evidence, verify the JSON contract, section readiness, report coverage,
  advisor-only separation, AI-readiness guardrails, and evidence lineage before treating the output
  as meeting-ready
- for report job evidence, verify idempotency behavior, product-safe status, append-only status
  events, and bounded cancellation before render/archive/completion

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
- report job lifecycle state is durable in PostgreSQL and configured by
  `REPORT_JOB_LEDGER_DATABASE_URL`; runtime readiness fails if the database or mandatory ledger
  schema is unavailable
- report job support queries are backed by indexes for idempotency lookup, tenant/region/time
  diagnostics, as-of-date filtering, portfolio-scope diagnostics, status queues, completion scans,
  request/job joins, and append-only event history
- native PostgreSQL partitioning is intentionally deferred until a later scale/retention RFC can
  preserve global idempotency semantics; the current ledger is partition-ready but not partitioned
- destructive purge, legal hold, and document-retention operations are not first-wave ledger
  features and must not be simulated with manual deletes in support workflows
- direct process port `8300` is useful for local debugging, but canonical cross-app validation
  should use `report.dev.lotus`
- Docker Compose uses `host.docker.internal` upstream URLs so the container can reach the
  host-published canonical upstream ports while callers continue to use `report.dev.lotus`
- Docker Compose starts a separate `lotus-report-postgres` service for local report job ledger
  parity; do not use a file database for runtime or integration evidence

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

Portfolio review report job proof:

```powershell
curl -X POST "http://127.0.0.1:8300/reports/portfolio-reviews" `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22" `
  -H "X-Actor-Id: advisor-123" `
  -H "X-Caller-Application: lotus-gateway" `
  -H "X-Tenant-Id: tenant-sg" `
  -H "X-Region: APAC" `
  -H "X-Booking-Center-Code: SG" `
  -H "X-Role: advisor" `
  -H "X-Correlation-ID: portfolio-review-job-local-proof" `
  -d "{\"portfolio_scope\":{\"portfolio_ids\":[\"PB_SG_GLOBAL_BAL_001\"]},\"as_of_date\":\"2026-04-22\",\"requested_output_formats\":[\"json\"],\"reporting_currency\":\"USD\",\"options\":{\"sections\":[\"OVERVIEW\",\"PERFORMANCE\",\"RISK_ANALYTICS\"],\"benchmark_code\":\"BMK_GLOBAL_BALANCED_60_40\"}}"
```

The expected response is a job handle with `report_request_id`, `report_job_id`, `status`,
`status_url`, and `idempotency_key`. Use `GET /reports/jobs/{job_id}` for status and
`GET /reports/jobs/{job_id}/events` for append-only lifecycle diagnostics. Use
`POST /reports/jobs/{job_id}/cancel` only before render/archive/completion phases.

## Key references

- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/standards/enterprise-readiness.md](../docs/standards/enterprise-readiness.md)
- [docs/standards/migration-contract.md](../docs/standards/migration-contract.md)
- [docs/standards/scalability-availability.md](../docs/standards/scalability-availability.md)
- [Portfolio Review Report](Portfolio-Review-Report)
