# API Surface

## Integration

- `GET /integration/capabilities`
  reporting capability publication for downstream consumers

## Aggregations

- `GET /aggregations/portfolios/{portfolio_id}`
  aggregated portfolio rows by as-of date

## Reports

- `POST /reports`
  report metadata generation
- `POST /reports/portfolios/{portfolio_id}/summary`
  lotus-report-owned portfolio summary payload
- `POST /reports/portfolios/{portfolio_id}/review`
  RFC-0002 first-class, machine-readable portfolio review report payload for client/advisor
  meetings

## Platform surfaces

- `/health`
- `/health/live`
- `/health/ready`
- `/metrics`
- `/docs`

## Current contract notes

- integration capability query parameters are canonical snake_case: `consumer_system`, `tenant_id`
- aggregation query currently uses camelCase alias `asOfDate`
- report summary/review query currently use camelCase alias `sectionLimit`
- summary/review service logic currently accepts both `as_of_date` and `asOfDate` request-body keys

## Request examples

Integration capabilities:

```bash
curl "http://127.0.0.1:8300/integration/capabilities?consumer_system=lotus-gateway&tenant_id=default"
```

Aggregations:

```bash
curl "http://127.0.0.1:8300/aggregations/portfolios/DEMO_DPM_EUR_001?asOfDate=2026-02-24&live=false"
```

Portfolio summary:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/DEMO_DPM_EUR_001/summary?sectionLimit=10" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: local-doc-probe" \
  -d "{\"asOfDate\":\"2026-02-24\",\"reportingCurrency\":\"EUR\"}"
```

Portfolio review:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/DEMO_DPM_EUR_001/review?sectionLimit=10" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: local-doc-probe" \
  -d "{\"as_of_date\":\"2026-02-24\",\"reporting_currency\":\"EUR\",\"benchmarkCode\":\"MSCI_ACWI\"}"
```

Canonical front-office portfolio review:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?sectionLimit=20" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: rfc-0002-local-proof" \
  -d "{\"asOfDate\":\"2026-04-22\",\"reportingCurrency\":\"USD\",\"benchmarkCode\":\"BMK_GLOBAL_BALANCED_60_40\",\"sections\":[\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

The review response is a typed report contract. It separates client-ready `client_sections` from
advisor-only `advisor_sections`, carries explicit section readiness states including
`not_applicable` for requested supporting sections with no applicable activity, includes
report-level `evidence`, exposes source-backed client/mandate profile context from lotus-core,
position cost/unrealized P&L, and YTD contribution where upstream services provide them. It also
includes deterministic `reportStructure`, `advisorBriefing`, and guarded `aiReadiness` metadata so
front-office consumers can organize a review meeting without treating report gaps as advice.
RFC-0002 capability keys are published through `GET /integration/capabilities`.

Detailed response-family guidance lives in [Portfolio Review Report](Portfolio-Review-Report).

Use these examples to keep the mixed query and request-body conventions visible until the public
surface is intentionally standardized.
