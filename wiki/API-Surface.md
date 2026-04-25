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
  internal durable portfolio review report job initiation; returns a job handle and, for PDF jobs,
  may advance through render completion before the response returns
- `GET /reports/jobs`
  internal operator-safe bounded search for report jobs by tenant, region, status, report type,
  portfolio id, as-of date, idempotency key, correlation id, and created-at window
- `GET /reports/jobs/{job_id}`
  internal product-safe report job status and diagnostics
- `GET /reports/jobs/{job_id}/events`
  internal append-only report job lifecycle event history
- `GET /reports/jobs/{job_id}/snapshot`
  internal durable report input snapshot lookup by job id
- `GET /reports/jobs/{job_id}/lineage`
  internal durable upstream-call lineage lookup by job id
- `POST /reports/jobs/{job_id}/cancel`
  internal bounded cancellation before render, archive, or completion phases
- `GET /reports/snapshots/{snapshot_id}`
  internal durable report input snapshot lookup by snapshot id
- `GET /reports/snapshots/{snapshot_id}/lineage`
  internal durable upstream-call lineage lookup by snapshot id

## Product-facing boundary

Front-office callers must use `lotus-gateway` for report job initiation and status:

- `POST /api/v1/reports/portfolio-reviews`
- `GET /api/v1/report-jobs`
- `GET /api/v1/report-jobs/{job_id}`
- `GET /api/v1/report-jobs/{job_id}/events`
- `POST /api/v1/report-jobs/{job_id}/cancel`

`lotus-report` owns the durable ledger and internal orchestration state. `lotus-gateway` owns the
product-facing ingress, caller context enforcement, and response posture for Workbench and other
front-office consumers.

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
- report job search requires at least one supported filter and is bounded by `limit`
- PDF-capable report jobs submit a governed render package to `lotus-render`; after successful
  render completion they hand the artifact and source-backed metadata to `lotus-archive`
- successful job initiation captures a durable snapshot and upstream lineage before the job reaches
  `data_ready`, and PDF jobs may then advance through `rendering`, `completed`, `archiving`, and
  `archived`
- archive retrieval, retention execution, legal hold, purge, and document distribution remain owned
  by `lotus-archive`
- snapshot and lineage endpoints are support-safe evidence APIs; they return hashes, posture, and
  summary metadata instead of raw upstream payload internals

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
curl -X POST "http://gateway.dev.lotus:8111/api/v1/reports/portfolio-reviews" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22" \
  -H "X-Actor-Id: advisor-123" \
  -H "X-Caller-Application: lotus-workbench" \
  -H "X-Tenant-Id: tenant-sg" \
  -H "X-Region: APAC" \
  -H "X-Booking-Center-Code: SG" \
  -H "X-Role: advisor" \
  -H "X-Correlation-ID: portfolio-review-job-local-proof" \
  -d "{\"portfolio_scope\":{\"portfolio_ids\":[\"PB_SG_GLOBAL_BAL_001\"]},\"as_of_date\":\"2026-04-22\",\"requested_output_formats\":[\"pdf\"],\"reporting_currency\":\"USD\",\"options\":{\"sections\":[\"OVERVIEW\",\"PERFORMANCE\",\"RISK_ANALYTICS\"],\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\"}}"
```

Report job status:

```bash
curl "http://gateway.dev.lotus:8111/api/v1/report-jobs/rjob_example"
```

Internal report snapshot lookup:

```bash
curl "http://127.0.0.1:8300/reports/jobs/rjob_example/snapshot" \
  -H "X-Actor-Id: support-operator-1" \
  -H "X-Caller-Application: lotus-gateway" \
  -H "X-Tenant-Id: tenant-sg" \
  -H "X-Region: APAC"
```

Internal report lineage lookup:

```bash
curl "http://127.0.0.1:8300/reports/jobs/rjob_example/lineage" \
  -H "X-Actor-Id: support-operator-1" \
  -H "X-Caller-Application: lotus-gateway" \
  -H "X-Tenant-Id: tenant-sg" \
  -H "X-Region: APAC"
```

Report job operational search:

```bash
curl "http://gateway.dev.lotus:8111/api/v1/report-jobs?tenantId=tenant-sg&region=APAC&portfolioId=PB_SG_GLOBAL_BAL_001&status=archived&limit=25" \
  -H "X-Actor-Id: support-operator-1" \
  -H "X-Tenant-Id: tenant-sg" \
  -H "X-Region: APAC"
```

For direct `lotus-report` proof after RFC-0101, the equivalent support-safe evidence paths are:

- `GET /reports/jobs/{job_id}/snapshot`
- `GET /reports/jobs/{job_id}/lineage`
- `GET /reports/snapshots/{snapshot_id}`
- `GET /reports/snapshots/{snapshot_id}/lineage`

Report job cancellation:

```bash
curl -X POST "http://gateway.dev.lotus:8111/api/v1/report-jobs/rjob_example/cancel" \
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
Report job capability keys and PDF render-submission posture are also published there once implementation-backed.

Detailed response-family guidance lives in [Portfolio Review Report](Portfolio-Review-Report).

Use these examples as the canonical public API shape. Swagger must not publish stale placeholder
reporting endpoints, RFC names, or duplicate camelCase aliases.
