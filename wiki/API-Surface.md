# API Surface

## Integration

- `GET /integration/capabilities`
  reporting capability publication for downstream consumers

## Aggregations

- `GET /aggregations/portfolios/{portfolio_id}`
  aggregated portfolio rows by as-of date

## Reports

- `POST /reports/portfolios/{portfolio_id}/summary`
  lotus-report-owned portfolio summary payload
- `POST /reports/portfolios/{portfolio_id}/review`
  machine-readable portfolio review report payload for client/advisor meetings
- `POST /reports/portfolio-reviews`
  durable portfolio review report job initiation; returns a job handle, not a rendered document
- `GET /reports/jobs/{job_id}`
  product-safe report job status and diagnostics
- `POST /reports/jobs/{job_id}/cancel`
  bounded cancellation before render, archive, or completion phases

## Platform surfaces

- `/health`
- `/health/live`
- `/health/ready`
- `/metrics`
- `/docs`

## Current contract notes

- integration capability query parameters are canonical snake_case: `consumer_system`, `tenant_id`
- aggregation query parameter is canonical snake_case: `as_of_date`
- report summary/review query parameters use canonical `section_limit`
- portfolio review request bodies use canonical snake_case fields only
- report job creation requires `Idempotency-Key`
- report job endpoints do not render PDFs or archive documents

## Request examples

Integration capabilities:

```bash
curl "http://127.0.0.1:8300/integration/capabilities?consumer_system=lotus-gateway&tenant_id=default"
```

Aggregations:

```bash
curl "http://127.0.0.1:8300/aggregations/portfolios/DEMO_DPM_EUR_001?as_of_date=2026-02-24&live=false"
```

Portfolio summary:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/DEMO_DPM_EUR_001/summary?section_limit=10" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: local-doc-probe" \
  -d "{\"as_of_date\":\"2026-02-24\",\"reporting_currency\":\"EUR\"}"
```

Portfolio review:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/DEMO_DPM_EUR_001/review?section_limit=10" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: local-doc-probe" \
  -d "{\"as_of_date\":\"2026-02-24\",\"reporting_currency\":\"EUR\",\"benchmark_code\":\"MSCI_ACWI\"}"
```

Canonical front-office portfolio review:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: portfolio-review-local-proof" \
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

Portfolio review report job:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolio-reviews" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22" \
  -H "X-Actor-Id: advisor-123" \
  -H "X-Caller-Application: lotus-gateway" \
  -H "X-Tenant-Id: tenant-sg" \
  -H "X-Region: APAC" \
  -H "X-Booking-Center-Code: SG" \
  -H "X-Role: advisor" \
  -H "X-Correlation-ID: portfolio-review-job-local-proof" \
  -d "{\"portfolio_scope\":{\"portfolio_ids\":[\"PB_SG_GLOBAL_BAL_001\"]},\"as_of_date\":\"2026-04-22\",\"requested_output_formats\":[\"json\"],\"reporting_currency\":\"USD\",\"options\":{\"sections\":[\"OVERVIEW\",\"PERFORMANCE\",\"RISK_ANALYTICS\"],\"benchmark_code\":\"BMK_GLOBAL_BALANCED_60_40\"}}"
```

Report job status:

```bash
curl "http://127.0.0.1:8300/reports/jobs/rjob_example"
```

Report job cancellation:

```bash
curl -X POST "http://127.0.0.1:8300/reports/jobs/rjob_example/cancel" \
  -H "X-Actor-Id: advisor-123" \
  -H "X-Correlation-ID: portfolio-review-job-cancel"
```

The review response is a typed report contract. It separates client-ready `client_sections` from
advisor-only `advisor_sections`, carries explicit section readiness states including
`not_applicable` for requested supporting sections with no applicable activity, includes
report-level `evidence`, exposes source-backed client/mandate profile context from lotus-core,
position cost/unrealized P&L, transaction-level realized P&L, and YTD contribution where upstream
services provide them. It also
includes deterministic `report_structure`, `advisor_briefing`, guarded `ai_readiness`, and
`upstream_capability_audit` metadata so front-office consumers can organize a review meeting without
treating report gaps as advice or silently losing upstream dependency gaps.
Portfolio review capability keys are published through `GET /integration/capabilities`.
Report job capability keys are also published there once implementation-backed.

Detailed response-family guidance lives in [Portfolio Review Report](Portfolio-Review-Report).

Use these examples as the canonical public API shape. Swagger must not publish stale placeholder
reporting endpoints, RFC names, or duplicate camelCase aliases.
