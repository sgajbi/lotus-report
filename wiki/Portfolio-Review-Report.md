# Portfolio Review Report

## Purpose

`POST /reports/portfolios/{portfolio_id}/review` is the portfolio review contract for
front-office client review meetings.

It is not a PDF renderer and it is not an advice engine. It produces a governed, machine-readable
review payload that Workbench, gateway, downstream reporting surfaces, and future document
renderers can consume without losing source lineage or supportability state.

For asynchronous job initiation, use `POST /reports/portfolio-reviews`. That route creates durable
request/job/status ledger records and returns a job handle. It does not render a document or archive
an output.

## Product Standard

The report should help a client advisor answer five meeting questions:

1. who is the client and what mandate is being reviewed?
2. what changed in the portfolio and why?
3. what drove performance, contribution, risk, concentration, income, and activity?
4. what must be discussed, challenged, or escalated before the client meeting?
5. which facts are sourced, partial, unavailable, or not yet supported?

The contract is deliberately strict about missing information. If an enterprise-grade review should
include suitability, target allocation, mandate restrictions, liquidity needs, open tax-lot
attribution, or jurisdiction-specific tax treatment but the source system has not provided that data,
the response marks the gap explicitly instead of inventing report content. Transaction-level
realized gain/loss is sourced from `lotus-core` transaction rows where present.

## Audience Model

The response separates audiences:

- `client_sections`
  client-ready report material
- `advisor_sections`
  advisor-only prompts, checks, and route targets
- `advisor_briefing`
  deterministic advisor meeting notes derived from sourced data and explicit gaps
- `ai_readiness`
  metadata for guarded AI assistance, not AI-generated advice

Advisor-only material must not be rendered into client-facing output unless a future product slice
explicitly changes that rule and adds the right approval controls.

## Source Authorities

`lotus-report` composes the report from domain-authoritative services:

| Source | Current report use | Ownership boundary |
| --- | --- | --- |
| `lotus-core` | portfolio summary, allocation, positions, transactions, portfolio detail, client profile and mandate context where available | Source of portfolio, booking, holding, transaction, and mandate facts |
| `lotus-performance` | workspace performance summary and YTD contribution | Source of performance analytics used by reporting |
| `lotus-risk` | risk analytics derived from the report review flow | Source of risk analytics |
| `lotus-report` | report shape, section ordering, readiness, coverage, observations, evidence, and meeting-pack composition | Reporting contract owner only |

## Request

Canonical local probe for the governed front-office portfolio:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: portfolio-review-local-proof" \
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

Request convention notes:

- Portfolio review uses canonical snake_case request fields and query parameters.
- `section_limit`, `as_of_date`, `reporting_currency`, and `benchmark_code` are the supported
  public names for this endpoint.
- CamelCase request aliases are not part of the governed portfolio review contract.

## Response Shape

Top-level response families:

| Field | Purpose |
| --- | --- |
| `contract_version`, `report_id`, `portfolio_id`, `as_of_date`, `generated_at`, `review_period`, `reporting_currency`, `audience` | report metadata, currency, period, and audience posture |
| `readiness` | overall readiness status and report-level reasons |
| `client_profile` | source-backed client, advisor, booking center, mandate, objective, risk exposure, horizon, leverage, status, open date, base currency, and cost-basis method |
| `key_figures` | normalized front-office figures for portfolio value, allocation, performance, contribution, risk, income/activity, holdings, P&L, transactions, and profile state |
| `client_sections` | ordered client-ready sections with readiness states and machine-readable items |
| `advisor_sections` | advisor-only deterministic checks, discussion prompts, and route targets |
| `report_coverage` | coverage map showing present, partial, unavailable, and not-sourced report families |
| `upstream_capability_audit` | machine-readable certification audit separating source-backed capabilities from upstream gaps |
| `review_observations` | review issues and advisor attention points derived from sourced figures and missing coverage |
| `report_structure` | recommended meeting-pack order for UI, document, or presentation consumers |
| `advisor_briefing` | deterministic advisor talking points and required checks |
| `ai_readiness` | guarded AI feature metadata and blocked AI use cases |
| `evidence` | source refs, lineage bundle, trust metadata, and domain-product context |

## Gold-Standard Figure Coverage

Current implementation-backed figures include:

- total market value and reporting currency
- allocation by asset class and geography
- YTD net return and period performance where sourced
- YTD contribution totals, top contributors, and detractors where sourced
- risk metrics, volatility, drawdown, Sharpe, value-at-risk, and concentration indicators where
  sourced or derivable
- position-level market value, cost basis, unrealized P&L, unrealized P&L percentage, product type,
  sector, country of risk, rating, liquidity tier, and held-since date where sourced
- income/activity totals and transaction categorization
- transaction-level realized gain/loss totals and transaction-row enrichment where sourced from
  `lotus-core`
- negative cash and concentration observations
- client profile and mandate context where sourced from `lotus-core`

Current explicit gaps:

- suitability determination
- target allocation and drift versus target
- mandate guideline tests and product restrictions
- client liquidity needs and review-cycle freshness
- open tax-lot attribution
- jurisdiction-specific tax treatment
- trade recommendations
- AI-generated client advice

These gaps are intentionally visible through `report_coverage`, `upstream_capability_audit`,
`review_observations`, and `ai_readiness`.

## AI Assistance Posture

The current response supports guarded AI assistance metadata only.

Supported future AI use cases:

- advisor meeting question suggestions grounded in response JSON
- plain-language section summary drafts grounded in response JSON
- exception explanation drafts grounded in sourced observations

Blocked current AI use cases:

- trade recommendations
- suitability determinations
- inferred client profiles
- mandate breach conclusions without authoritative mandate evidence
- client-facing advice text without governed approval controls

Any future AI slice should route through governed `lotus-ai` capability, preserve source refs, cite
supportability state, and require human advisor approval for client-facing material.

## Regulatory And Control Posture

The report is designed for private-banking review discipline:

- distinguish sourced fact from missing evidence
- keep advisor-only material separate from client report material
- expose suitability and mandate-control gaps rather than burying them
- preserve source refs, correlation, lineage, readiness, and trust metadata
- avoid report-side inference of client profile, advice, or trade recommendations

This is the right baseline for MAS/HKMA-style operating expectations, but the endpoint itself is
not a regulator-certified suitability engine. Regulatory controls must be completed through the
authoritative advisory, mandate, approval, audit, and disclosure workflows before the report can
claim end-to-end suitability or advice compliance.

External reference anchors for future control mapping:

- MAS Notice FAA-N16, `Notice on Recommendations on Investment Products`
  https://www.mas.gov.sg/regulation/notices/notice-faa-n16
- SFC suitability requirement overview
  https://www.sfc.hk/en/Rules-and-standards/Suitability-requirement
- HKMA private-banking investment-product selling guidance repository entry
  https://brdr.hkma.gov.hk/eng/doc-ldg/docId/20120612-1-EN

Use the current official version of each reference when implementing formal compliance controls.
This page is product and engineering guidance, not legal advice.

## Operating Proof

Use repo-native gates before sharing report evidence:

```powershell
make check
make test-integration
make test-e2e
make test-coverage
make docker-build
```

For live local proof:

```powershell
docker compose up -d --build
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20" `
  -H "Content-Type: application/json" `
  -H "X-Correlation-ID: portfolio-review-local-proof" `
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

Do not treat a visually pleasant output as sufficient proof. A review report is only acceptable
when the JSON contract, source coverage, key figures, gaps, evidence, and CI posture are all
healthy.

## Extension Rules

When extending the report:

1. add the upstream source contract first or identify an existing authoritative source,
2. add the typed response fields and OpenAPI example,
3. add high-value unit tests for computation and gap behavior,
4. add integration or e2e proof when the surface changes,
5. update `docs/supported-features.md` only after implementation exists,
6. update this wiki page only for durable operator or product truth,
7. keep advisor-only, client-ready, and AI-assisted material clearly separated.
