# Portfolio Review Report

## Current Scope

Current scope: implementation-backed portfolio review payload behavior for direct service,
gateway, Workbench, and asynchronous report-job consumers. Evidence posture: code and tests back
the JSON contract, section readiness, source lineage, transaction-window budgets, render-package
handoff, and archive handoff boundaries described here.

| Reader | Start Here | Decision Supported |
| --- | --- | --- |
| Product and business | Product Standard | What the review can truthfully claim in a client-advisor meeting |
| Operations and support | Runtime And Evidence | How to prove source-backed output and diagnose partial supportability |
| Engineering and agents | Contract Shape | Which fields, sections, ownership boundaries, and tests protect the route |

## Purpose

`POST /reports/portfolios/{portfolio_id}/review` is the portfolio review contract for
front-office client review meetings.

It is not a PDF renderer and it is not an advice engine. It produces a governed, machine-readable
review payload that Workbench, gateway, downstream reporting surfaces, and future document
renderers can consume without losing source lineage or supportability state.

For asynchronous job initiation, use `POST /reports/portfolio-reviews`. That route creates durable
request/job/status ledger records, captures the immutable input snapshot and upstream lineage, and
for PDF requests submits a governed render package to `lotus-render`. After successful render
completion, it hands the rendered artifact and source-backed metadata to `lotus-archive` and records
the archive outcome separately from render completion.

The render handoff posture is explicit:

- `lotus-report` owns data assembly, snapshot capture, lineage, and render-package composition
- `lotus-render` owns PDF execution, artifact hashing, and support-safe render diagnostics
- `lotus-archive` owns archived document identity, retrieval, retention execution, legal hold,
  purge, and storage diagnostics
- the supported repeatability claim is bounded runtime-envelope determinism via fingerprint, not
  byte-stable PDF identity
- archive retrieval, legal hold, purge, replay, rerender, regenerate, and document distribution remain
  outside `lotus-report` handoff scope

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
realized gain/loss is sourced from `lotus-core` transaction rows where present. Summary P&L uses
the same source-backed posture: it does not derive unrealized or total P&L from market-value minus
invested-value totals when source P&L fields are absent.

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

For asynchronous `POST /reports/portfolio-reviews` jobs, a caller may include a
`proposal_narrative_package` emitted by `lotus-advise`. The package is accepted only when
`lotus-advise` has already marked it `INCLUDED_REVIEWED_NARRATIVE`, the review state is
`APPROVED_FOR_ADVISOR_USE`, and `source_lineage.source_narrative_hash` is present. `lotus-report`
then preserves the package in the immutable snapshot, adds `lotus-advise` to the lineage summary,
and projects a bounded `reviewed_advisory_narrative` block into the render package. It does not
approve, rewrite, summarize, or infer advisory content.

RFC-0024 also allows an optional `proposal_memo_package` emitted by `lotus-advise`. The package is
accepted only when it is marked `INCLUDED_ADVISOR_PROPOSAL_MEMO`, carries
`APPROVE_FOR_ADVISOR_USE` review posture, SHA-256 memo/source hashes, memo sections, and blocked
client-ready posture. `lotus-report` preserves the package in the immutable snapshot, projects a
bounded `advisor_proposal_memo` block into the render package, and includes support-safe memo
metadata in archive handoff without approving, rewriting, or inferring memo facts.

## Source Authorities

`lotus-report` composes the report from domain-authoritative services:

| Source | Current report use | Ownership boundary |
| --- | --- | --- |
| `lotus-core` | portfolio summary, allocation, positions, transactions, portfolio detail, client profile and mandate context where available | Source of portfolio, booking, holding, transaction, and mandate facts |
| `lotus-performance` | workspace performance summary and YTD contribution | Source of performance analytics used by reporting |
| `lotus-risk` | risk analytics derived from the report review flow | Source of risk analytics |
| `lotus-advise` | optional approved `proposal_narrative_package` and `proposal_memo_package` on asynchronous portfolio-review jobs | Source of advisory narrative and memo approval, review state, source hashes, sections, guardrails, limitations, disclosures, and client-ready blocked posture |
| `lotus-report` | report shape, section ordering, readiness, coverage, observations, evidence, and meeting-pack composition | Reporting contract owner only |

Domain-product certification boundary: current repo-native consumer declarations govern the
`lotus-core` evidence dependencies. `lotus-performance` and `lotus-risk` remain live runtime source
services for analytics sections, but analytics-enriched `ClientReportEvidencePack` evidence is
partial and blocked for mesh certification until those producer declarations approve `lotus-report`
as a governed consumer.

## Request

Canonical local probe for the governed front-office portfolio:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: portfolio-review-local-proof" \
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
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
- `HoldingsAsOf:v1` source-product metadata, data-quality posture, reconciliation posture, latest
  evidence timestamp, restatement/source-lineage fields, maturity date, position-state status, row
  snapshot identity, and row source identifiers where `lotus-core` provides them
- income/activity totals and transaction categorization
- transaction-level realized gain/loss totals and transaction-row enrichment where sourced from
  `lotus-core`
- summary `pnlSummary` fields for sourced position unrealized P&L, sourced transaction realized
  gain/loss, component status, total status, source methodology, and supportability notes
- bounded `TransactionLedgerWindow:v1` supportability; oversized windows are truncated by
  `REPORT_TRANSACTION_MAX_ROWS` and `REPORT_TRANSACTION_MAX_PAGES`, and partial, unknown, paged, or
  trust-metadata-incomplete core windows are marked partial instead of appearing complete
- transaction rows preserve sourced settlement date, linked transaction-cost evidence, linked
  cashflow evidence, source-product metadata, data-quality posture, reconciliation posture, latest
  evidence timestamp, and restatement/source-lineage fields where `lotus-core` provides them
- tax-lot and jurisdiction-specific tax treatment remain unsupported until backed by
  `PortfolioTaxLotWindow:v1` source transaction, lot status, quantity, cost-basis, paging, and
  calculation-policy lineage evidence
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
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

For the governed RFC-0102 render-boundary proof pack:

```powershell
python scripts/rfc_0102_live_evidence.py
```

That script produces a clean evidence directory under `output/rfc-0102-live-evidence-*` containing
the exact `lotus-report` to `lotus-render` package, live render responses, render metadata, status
lookups, negative-path failures, runtime logs, and the bounded-determinism comparison summary.

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

## Advisor commentary section (ADVISOR_COMMENTARY)

The portfolio review report can carry a governed narrative section sourced exclusively from an
**accepted Performance Advisor Brief** (lotus-ai `advisor_brief.pack@v1` accepted-output
projection, issue #166). Ordering rules:

- Request it via `options.sections` including `ADVISOR_COMMENTARY` **and**
  `options.advisor_brief_run_id` naming the accepted run. An order that selects the section
  without the run id is rejected at acceptance (single orders and batches alike) - lotus-report
  never chooses a brief implicitly.
- **Temporary render gate**: PDF orders refuse the section explicitly until the lotus-render
  template renders it - a PDF that silently omitted an ordered section would be a misleading
  client document. Order `json` output for the section until then.
- A lotus-ai 401/403 (the `lotus-report` caller missing or inactive in the lotus-ai
  access-control registry) fails the capture retryable and is an environment fault to fix in
  lotus-ai, not a section posture.
- At capture time the accepted output is resolved by run id from lotus-ai (recorded as durable
  upstream-call lineage like every other source read). The exact reviewed narrative is composed
  unmodified - lotus-report never regenerates, edits, or re-reviews AI content.
- The section **fails closed without failing the report** on definitive postures, with one
  bounded reason recorded on the job (`job_advisor_commentary_unavailable` event, snapshot
  package, and lineage summary): `advisor_brief_not_reviewed` (run not completed/accepted, or
  superseded), `advisor_brief_not_found` (unknown run or unretrievable output),
  `advisor_brief_context_mismatch` (the brief asserts a portfolio, as-of date, or reporting
  currency that differs from the report's; nulls mean "not asserted" and never conflict), and
  `ai_disclosure_policy_unavailable` (accepting reviewer identity or content hash missing, so
  the mandated disclosure line cannot be rendered truthfully).
- Transport-level lotus-ai unavailability fails the capture with the standard retryable
  posture instead - retrying can succeed, so the pack does not silently ship without a section
  the caller ordered.
- When included, the render package and JSON output carry the summary, talking points, risks
  and exceptions, the review identity, the pinned `content_hash`, and the AI-assistance
  disclosure line; archive metadata keeps the run id, request id, reviewer, review time, and
  content hash.
- lotus-report calls lotus-ai as registered caller `lotus-report` (`X-Caller-App`); that
  caller must be registered and active in the lotus-ai access-control registry for the
  environment.
