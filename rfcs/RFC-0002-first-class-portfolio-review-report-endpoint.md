# RFC-0002: First-Class Portfolio Review Report Endpoint

## Status

Done.

Slices 1 through 11 and the final hardening passes are complete. The first-class review contract is
shipped in `lotus-report`, the Workbench-facing gateway contract boundary is validated in companion
gateway PR `sgajbi/lotus-gateway#145`, and RFC-0002 is ready for merge. Remaining work after merge
is limited to publishing the repo-authored wiki source and normal branch cleanup.

## Implementation Classification

Enhancement of an existing route.

`lotus-report` already exposes `POST /reports/portfolios/{portfolio_id}/review` and composes useful
upstream material. Before RFC-0002, that route was an implementation-backed legacy aggregation
capability rather than the stable, typed, evidence-backed advisor/client meeting contract defined by
this RFC.

The implementation preserves truthful compatibility for existing consumers while introducing a
versioned first-class report contract.

## Date

2026-04-22

## Owner Repository

`lotus-report`

## Repository Role

`lotus-report` is the Lotus reporting and aggregation service. It builds reporting-oriented read
models and reporting payloads from authoritative upstream services.

This RFC treats `lotus-report` as a reporting capability service with front-office consumers. It is
not a domain authority for portfolio books, transactions, performance methodology, risk models,
advisory proposals, or management actions.

## Problem Statement

`lotus-report` already exposed a portfolio review route, but the pre-RFC payload was a general
aggregation response rather than a first-class advisor/client meeting artifact. It did not define a
stable report contract, evidence posture, presentation order, client-ready versus advisor-only
sections, supportability states, or the discussion structure expected in a private-bank portfolio
review.

The enterprise product need is a portfolio review endpoint that a client advisor can confidently use
before and during a portfolio review meeting. The report must be concise enough for a client
conversation, complete enough for advisor preparation, and evidence-backed enough for audit,
supportability, downstream Workbench presentation, and future PDF/report rendering.

## Current Lotus Implementation Reality

Current implementation evidence after Slice 11:

1. `src/app/routers/reports.py` exposes
   `POST /reports/portfolios/{portfolio_id}/review`.
2. `src/app/services/reporting_read_service.py` composes the review payload from:
   - `lotus-core` portfolio detail, portfolio summary, allocation, positions, and transaction data,
   - `lotus-performance` workspace summary data,
   - `lotus-risk` risk analytics when enough return history is available.
3. `tests/unit/test_reporting_read_service.py`,
   `tests/unit/test_reporting_read_service_additional.py`,
   `tests/integration/test_api.py`, and `tests/e2e/test_reporting_workflows.py` cover the current
   route and service behavior.
4. `GET /integration/capabilities` publishes the legacy compatibility key and the
   implementation-backed RFC-0002 feature keys.
5. `contracts/domain-data-products/lotus-report-products.v1.json` declares
   `lotus-report:ClientReportEvidencePack:v1` for the portfolio review route, including partial
   completeness policy.
6. Companion gateway PR `sgajbi/lotus-gateway#145` proves the Workbench-facing gateway boundary
   preserves partial/unavailable section states and advisor-only separation.

Original gaps addressed by Slices 1 through 11:

1. the route returned an untyped reporting payload rather than a versioned review-report contract,
2. section readiness was implicit or represented as `None` rather than explicit report posture,
3. performance and risk figures did not carry enough report-level methodology, benchmark, fee
   basis, annualization, and source-provenance metadata for a client meeting artifact,
4. advisor-only preparation material was not separated from client-ready report material,
5. partial upstream failure was not presented as a first-class meeting-report state,
6. Workbench and gateway did not have a stable first-class portfolio review pack contract to
   consume,
7. supported-feature truth did not distinguish current legacy review aggregation from the
   RFC-0002 first-class review report,
8. wiki/operator material did not explain target client/advisor review-report semantics.
9. portfolio-level client/advisor/mandate context, meeting structure, advisor briefing, and
   governed AI-readiness metadata were not explicit enough for a high-end private banking review
   pack.

No RFC-0002 product behavior remains in planned state. Future portfolio review extensions must be
opened as new planned feature rows before they are promoted to implementation-backed product truth.

## Research Basis

Public material from large wealth managers points to consistent portfolio review expectations:

1. Merrill's Portfolio Review Report overview frames the report as a conversation artifact covering
   portfolio overview, asset allocation, performance analysis, holdings, discussion prompts, and
   explicit performance/disclaimer language.
   Source: `https://olui2.fs.ml.com/Publish/Content/application/pdf/GWMOL/MerrillEdgeAdvisoryCenterPortfolioReviewReportOverview.pdf`
2. J.P. Morgan's client tooling exposes asset allocation with percentages and amounts, portfolio
   performance over selectable time periods, overview/detail views, and benchmark/index comparison.
   Source: `https://privatebank.jpmorgan.com/nam/en/about-us/online-tools/how-to-pages/portfolio-performance`
3. UBS Asset Wizard positions portfolio reporting as a strategic cockpit covering allocation,
   performance, risk, investment structure, guideline monitoring, private equity reporting,
   sustainability analytics, customizable views, and booklet reports.
   Source: `https://www.ubs.com/global/en/wealthmanagement/topics/asset-servicing/assetwizard.html`
4. Citi Private Bank emphasizes holistic portfolio analytics, risk exposures, opportunities,
   actionable insights, customized strategy, risk/return goals, liquidity, geography, currency
   preferences, tactical adjustments, and regular risk management.
   Sources:
   `https://www.privatebank.citibank.com/we-offer/portfolio-analytics`
   and `https://www.privatebank.citibank.com/we-offer/investments`
5. Bank of America / Merrill material reinforces that advisor review work must align risk profiles,
   goals, objectives, asset allocations, and client performance reporting. Risk willingness and
   financial ability should be reviewed as circumstances change.
   Sources:
   `https://careers.bankofamerica.com/en-us/job-detail/26006627/private-wealth-investment-management-specialist-chicago-illinois-united-states-esomprank-eucbtxqhpx-13`
   and `https://www.privatebank.bankofamerica.com/articles/what-is-risk-tolerance.html`
6. Performance presentation governance should avoid misleading claims, disclose benchmark and fee
   basis clearly, and preserve fair and complete presentation. CFA Institute GIPS guidance and SEC
   marketing-rule material reinforce the need for documented calculation methodology, fair
   presentation, benchmark clarity, fee treatment, and required disclosures.
   Sources:
   `https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/overview-of-the-global-investment-performance-standards`
   `https://www.sec.gov/newsroom/press-releases/2020-334`
   `https://www.sec.gov/compliance/complianceoutreach/compliance-outreach-program-investment-adviser-investment-company-chief-compliance-officers/compliance-outreach-program-national-exam-program-alerts-other-notices-0`

## Source-Derived Design Findings

The report should be shaped as a meeting pack rather than a raw API dump.

The consistent external pattern is:

1. lead with a short executive review that helps an advisor run the meeting,
2. show current position, allocation, performance, and risk before deep appendices,
3. compare performance against the right benchmark and period basis,
4. label gross/net, fee, currency, and annualization treatment explicitly,
5. show risk, concentration, liquidity, and exposure posture as client-relevant discussion points,
6. include holdings, transaction, income, and cash detail as supporting evidence rather than first
   screen material,
7. separate client-ready content from advisor-only preparation, prompts, and follow-up planning,
8. make every figure traceable to a source and an as-of date,
9. present partial or missing data as a visible report state rather than omitting it silently,
10. preserve methodology and disclosure context in the same report artifact.

For Lotus, a complete response is not merely "all sections present". A complete response is a
response where each section says whether it is ready, partial, unavailable, or intentionally
omitted, and why.

## Decision

Upgrade `lotus-report` portfolio review into a first-class, versioned report contract centered on
advisor/client review meetings.

The canonical endpoint remains:

```text
POST /reports/portfolios/{portfolio_id}/review
```

The endpoint will return a typed `PortfolioReviewReportResponse` rather than an unstructured
`dict[str, Any]`. The response will include:

1. report identity and metadata,
2. review scope and meeting context,
3. client-ready sections,
4. advisor-only preparation sections,
5. section readiness and supportability states,
6. evidence references,
7. upstream source freshness and partial-failure posture,
8. methodology and disclosure metadata,
9. route targets for Workbench drilldown and eventual PDF/report rendering.

This RFC does not move domain authority into `lotus-report`. It keeps:

1. portfolio and transaction truth in `lotus-core`,
2. performance truth in `lotus-performance`,
3. risk truth in `lotus-risk`,
4. proposal/advisory truth in `lotus-advise`,
5. management action truth in `lotus-manage`,
6. reporting aggregation, report shape, and client-report evidence packaging in `lotus-report`.

## Scope

In scope:

1. versioned request and response models for the first-class portfolio review report,
2. stable section ordering for advisor/client review meetings,
3. explicit section readiness states,
4. client-ready versus advisor-only section separation,
5. methodology, disclosure, source freshness, and lineage metadata,
6. deterministic discussion prompts grounded only in report facts,
7. Workbench and gateway consumption readiness after the report contract stabilizes,
8. domain-data-product declaration alignment for `ClientReportEvidencePack`,
9. repository-supported feature truth that separates implementation-backed features from planned
   RFC work,
10. documentation, wiki, and agent-context updates needed to make the implementation operable.

Out of scope:

1. a full advisor cockpit in `lotus-report`,
2. proposal creation, order creation, approvals, or management actions,
3. making `lotus-report` the authority for portfolio, performance, risk, advisory, or management
   data,
4. PDF generation in the first implementation slice unless the JSON contract is already stable and
   tested,
5. semantic search, embeddings, or chat-agent behavior,
6. LLM-generated client language,
7. GIPS, SEC, jurisdiction-specific, or bank-policy compliance claims without separate
   legal/compliance review.

## Architecture Direction

### Service Boundaries

`lotus-report` must compose and package report-ready information without re-owning upstream truth.

Required boundary behavior:

1. source portfolio, holdings, cash, allocation, and transaction state from `lotus-core`,
2. source performance figures, return windows, benchmark comparisons, and supported performance
   methodology from `lotus-performance`,
3. source risk metrics and risk supportability from `lotus-risk`,
4. reference advisory proposals or management actions only through route targets or identifiers when
   those upstream services expose source-backed material,
5. keep report-only derivations explicit, limited, and labeled.

### Contract Shape

The first-class response must be a typed Pydantic contract with a stable `contract_version`.

Required contract properties:

1. no untyped `dict[str, Any]` response model for the RFC-0002 route after the typed contract slice,
2. explicit request model and response model names,
3. stable section identifiers,
4. stable supportability-state vocabulary,
5. normalized machine-readable `client_sections[].items` rather than empty presentation
   placeholders,
6. OpenAPI examples covering ready, partial, and unavailable states,
7. source and methodology metadata included in the response rather than hidden in logs.

### Request Compatibility

Canonical new contract fields should use snake_case. Existing compatibility aliases may remain only
when they are tested and documented.

Compatibility expectations:

1. preserve existing `as_of_date` and `asOfDate` behavior during the transition,
2. explicitly decide whether `sectionLimit` remains as a compatibility alias or is superseded by
   `section_limit`,
3. document the canonical field names in the API examples,
4. avoid adding new aliases without vocabulary review.

### Data Mesh And Evidence

`ClientReportEvidencePack` must become an implementation-backed data product only when route
behavior, contract declaration, trust telemetry, tests, and documentation all agree.

Required data mesh behavior:

1. update `contracts/domain-data-products/lotus-report-products.v1.json` only when the implemented
   route and product declaration match,
2. validate governed consumer declarations for upstream data products used by the report,
3. include required trust metadata such as product name, product version, tenant id, generated-at,
   as-of date, reconciliation status, data quality status, lineage bundle id, and correlation id,
4. keep customer-consumable evidence separate from operator-only or restricted telemetry,
5. run `make domain-product-validate` when declarations or trust telemetry change.

### Front-Office Consumption

The JSON contract must be presentation-ready without hardcoding a specific renderer.

Workbench and gateway must not consume a speculative contract. Gateway and Workbench work starts
only after the `lotus-report` contract is typed, tested, and documented.

### Latency And Runtime Posture

The report is front-office facing. The implementation must measure endpoint latency during local and
CI validation once code changes begin.

If live upstream composition cannot provide acceptable user experience for advisor meeting use, the
implementation must introduce a governed prepared-report/cache design or split it into a separate
RFC. The endpoint must not hide slow or partial upstream behavior behind optimistic client-ready
states.

## Supported Features Registry

The durable supported-features source for `lotus-report` is
`docs/supported-features.md`.

RFC-0002 implementation must update that file whenever a feature moves between these states:

1. `implementation-backed`: code, tests, API contract, docs, and operational evidence exist,
2. `planned`: accepted or proposed RFC work that is not yet shipped,
3. `deprecated`: still present but scheduled for removal or replacement,
4. `not-supported`: explicitly out of scope.

Rules:

1. do not describe RFC-0002 capabilities as implementation-backed until they are actually delivered,
2. keep `GET /integration/capabilities` feature keys aligned with the supported-features file,
3. keep wiki pages concise and operator-facing; do not duplicate full RFC content in the wiki,
4. update supported-features material in the same PR that enables a capability,
5. in the final RFC closure slice, confirm every delivered feature has implementation-backed
   wording and evidence pointers.

RFC-0002 feature keys:

1. `lotus-report.reporting.portfolio_review.first_class.v1`
2. `lotus-report.reporting.portfolio_review.section_readiness.v1`
3. `lotus-report.reporting.portfolio_review.evidence_pack.v1`
4. `lotus-report.reporting.portfolio_review.advisor_sections.v1`
5. `lotus-report.reporting.portfolio_review.workbench_ready.v1`
6. `lotus-report.reporting.portfolio_review.position_pnl.v1`
7. `lotus-report.reporting.portfolio_review.performance_contribution.v1`
8. `lotus-report.reporting.portfolio_review.client_profile.v1`
9. `lotus-report.reporting.portfolio_review.advisor_briefing.v1`
10. `lotus-report.reporting.portfolio_review.ai_readiness.v1`

These keys were promoted to implementation-backed in Slice 9 after code, tests, API contract
evidence, supported-features updates, and GitHub validation existed. Later hardening passes promoted
source-backed P&L/contribution, client profile, advisor briefing, and AI-readiness metadata only
after implementation and tests existed. Future extensions must start as planned rows in
`docs/supported-features.md`.

## Target Review Report Structure

The report should be ordered for a real advisor-led meeting:

1. **Cover And Review Scope**
   - portfolio id, client/household id when available, advisor id when available,
   - reporting currency,
   - as-of date,
   - review period,
   - generated timestamp,
   - contract version,
   - source freshness summary.

2. **Executive Review Summary**
   - total market value,
   - net flows,
   - period return and benchmark comparison,
   - top positive and negative drivers,
   - primary risk posture,
   - open review items,
   - report-ready status.

3. **Client Objectives And Mandate Fit**
   - risk profile/risk tolerance reference when source-backed,
   - mandate or strategy label,
   - target allocation reference when source-backed,
   - review of current allocation against risk/goal framing,
   - explicit unavailable state when objective or mandate data is not present.

4. **Asset Allocation And Portfolio Construction**
   - current allocation by asset class,
   - optional country, sector, currency, issuer, rating, product type views,
   - current versus target or model allocation when available,
   - drift and concentration observations,
   - look-through supportability where available.

5. **Performance Review**
   - MTD, QTD, YTD, trailing, since-inception windows where source-backed,
   - net and gross return basis where available,
   - benchmark comparison,
   - contribution/attribution summaries,
   - cash-flow impact,
   - fee basis and return methodology labels,
   - no annualization for sub-year periods unless upstream explicitly supports it.

6. **Risk Review**
   - volatility,
   - drawdown,
   - value-at-risk or equivalent risk figure where source-backed,
   - Sharpe or risk-adjusted return where supportability is valid,
   - concentration and issuer/asset-class exposure,
   - benchmark-relative risk posture when benchmark context is available,
   - supportability notes for partial risk-free, benchmark, or historical coverage.

7. **Income, Cash, And Activity**
   - cash balance and cash weight,
   - dividend and interest income,
   - fees, taxes, transfers, deposits, withdrawals,
   - notable recent activity,
   - liquidity posture where source-backed.

8. **Holdings And Transactions Appendix**
   - top holdings,
   - holdings by asset class,
   - recent transactions,
   - pagination/section-limit controls,
   - official-record disclaimer where holdings may differ from custody statements.

9. **Recommendations And Follow-Up**
   - deterministic advisor discussion prompts grounded in report facts,
   - report-backed next actions,
   - links to Workbench, proposal, risk, performance, and action-register surfaces,
   - advisor-only notes separated from client-ready copy.

10. **Evidence, Methodology, And Disclosures**
    - source services and entity ids,
    - source as-of dates,
    - lineage and evidence bundle ids,
    - calculation basis,
    - benchmark identity,
    - fee/gross/net labels,
    - generated-at timestamp,
    - regulatory and risk disclosures.

## Response Contract Sketch

```json
{
  "contract_version": "v1",
  "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
  "portfolio_id": "PB_SG_GLOBAL_BAL_001",
  "as_of_date": "2026-04-22",
  "review_period": {
    "start_date": "2026-01-01",
    "end_date": "2026-04-22",
    "label": "YTD"
  },
  "reporting_currency": "USD",
  "generated_at": "2026-04-22T00:00:00Z",
  "audience": {
    "client_ready": true,
    "advisor_only_sections_present": true
  },
  "readiness": {
    "status": "partial",
    "reason": "Risk benchmark-relative metrics are unavailable for the selected window."
  },
  "client_sections": [
    {
      "section_id": "executive_summary",
      "title": "Executive Review Summary",
      "status": "ready",
      "items": []
    }
  ],
  "advisor_sections": [
    {
      "section_id": "discussion_prompts",
      "title": "Advisor Discussion Prompts",
      "status": "ready",
      "items": []
    }
  ],
  "evidence": {
    "lineage_bundle_id": "lineage:portfolio-review:...",
    "source_refs": [],
    "partial_failures": []
  },
  "methodology": {
    "performance_basis": "NET",
    "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
    "fee_treatment": "net_of_fees_where_available",
    "return_methodology": "time_weighted_return"
  },
  "disclosures": []
}
```

## Section State Model

Every section must carry one of these states:

1. `ready`: source-backed content is present and suitable for the intended audience,
2. `partial`: some source-backed content is present but important dependencies are missing,
3. `unavailable`: the section is expected by the contract but cannot be produced for the request,
4. `omitted_by_request`: the caller intentionally excluded the section,
5. `not_applicable`: the section is structurally valid but does not apply to this portfolio or
   review period.

Every `partial`, `unavailable`, or `not_applicable` state must include a reason code and human-safe
message.

## Presentation Requirements

The endpoint must support both Workbench and printable/PDF renderers without hardcoding a specific
visual format.

Presentation rules:

1. client-ready sections must avoid unsupported conclusions and speculative language,
2. advisor-only sections must be explicitly labeled and separable,
3. each numerical claim must carry a source reference or section-level source reference,
4. performance must clearly label period, gross/net basis, benchmark, fee treatment, and
   annualization posture,
5. risk metrics must include supportability notes when dependencies are missing,
6. holdings and balances must carry official-record caveats when the report is not the book of
   record,
7. unavailable or partial sections must be visible rather than silently omitted,
8. report order must put summary and decisions first, appendices later,
9. route targets must be identifiers and links, not commands that mutate upstream services.

## Implementation Plan

Each slice must close with updated tests, traceability notes, and truthful evidence. A later slice
may split into smaller PRs if the implementation becomes broad, but the order below is the intended
delivery sequence.

### Slice 0: RFC, Branch, And Research Closure

Goal: create the governed implementation baseline before code changes.

Required work:

1. finalize this RFC and supporting docs,
2. keep the work on a feature branch, not `main`,
3. create and push a remote feature branch,
4. open or update a draft PR when implementation begins so GitHub checks can run asynchronously,
5. record source-based report requirements,
6. confirm existing `/reports/portfolios/{portfolio_id}/review` gaps,
7. decide whether `ClientReportEvidencePack` route declaration should align to this endpoint or a
   dedicated evidence-pack endpoint.

Exit criteria:

1. RFC is implementation-ready,
2. branch is remote-backed,
3. docs-only validation has passed or any gap is explicitly recorded,
4. no code implementation has begun under this RFC.

### Slice 1: Cleanup And Structure

Goal: reduce repository friction before adding the first-class contract.

Required work:

1. remove dead code encountered in the reporting route, service, models, tests, or docs touched by
   this RFC,
2. improve repository structure where needed without broad unrelated refactoring,
3. improve document structure and reduce sprawl,
4. move the right long-lived operator material to the repo-local `wiki/` source,
5. avoid duplicate documentation across repo docs and wiki pages,
6. ensure the wiki is actually published and usable after merge,
7. keep RFC detail in `rfcs/`, implementation evidence in docs/tests/contracts, and concise
   operator guidance in `wiki/`.

Exit criteria:

1. touched modules have no obvious dead helpers, duplicate mapping branches, or stale comments,
2. documentation has clear ownership boundaries between RFC, docs, and wiki,
3. `lotus-platform/automation/Sync-RepoWikis.ps1 -CheckOnly -Repository lotus-report` has been run
   before merge when wiki source changes,
4. post-merge wiki publication is completed through
   `lotus-platform/automation/Sync-RepoWikis.ps1 -Publish -Repository lotus-report`.

### Slice 2: Typed Review Contract

Goal: make the route contract explicit and OpenAPI-governed.

Required work:

1. add Pydantic request/response models for `PortfolioReviewReportRequest` and
   `PortfolioReviewReportResponse`,
2. replace `response_model=dict[str, Any]` on the review route,
3. preserve compatibility with current `as_of_date` and `asOfDate` aliases during transition,
4. make `section_limit`/`sectionLimit` behavior explicit,
5. add contract examples for ready, partial, and unavailable report states,
6. add contract tests for schema, required fields, section states, and alias behavior.

Exit criteria:

1. OpenAPI exposes the typed response,
2. contract tests fail if section state, evidence, methodology, or audience fields disappear,
3. compatibility behavior is covered by tests and docs.

### Slice 3: Meeting-Oriented Section Model

Goal: make the response usable as an advisor/client meeting pack.

Required work:

1. implement client-ready and advisor-only sections,
2. preserve canonical section ordering,
3. add explicit `ready`, `partial`, `unavailable`, `omitted_by_request`, and `not_applicable`
   states,
4. add deterministic reason codes for non-ready sections,
5. add unit tests for requested-section filtering and section-limit behavior.

Exit criteria:

1. report consumers can render the first screen without guessing section order,
2. advisor-only material cannot appear in client-ready sections by accident,
3. all non-ready sections carry reason codes and safe messages.

### Slice 4: Performance And Benchmark Hardening

Goal: make performance reporting presentation-safe and source-backed.

Required work:

1. use `lotus-performance` workspace summary as the performance authority,
2. add benchmark identity and gross/net/fee-treatment labels,
3. add no-annualization guard for sub-year periods unless explicitly supported by upstream,
4. carry period basis, return methodology, and source freshness into report methodology,
5. add tests for partial performance upstream failure and unsupported benchmark posture.

Exit criteria:

1. performance values are never presented without period, currency, benchmark, and methodology
   context where applicable,
2. unsupported performance windows produce visible partial/unavailable states,
3. local derivation, if any, is labeled and covered by tests.

### Slice 5: Risk Review Hardening

Goal: make risk reporting supportable and source-backed.

Required work:

1. use `lotus-risk` for risk metrics and supportability,
2. avoid deriving risk metrics locally from performance returns except as an explicitly documented
   fallback labeled as limited,
3. add concentration, drawdown, volatility, value-at-risk, and risk-adjusted-return fields only
   when source-backed,
4. add supportability notes for missing risk-free rate, missing benchmark, missing return history,
   and risk upstream failure,
5. add tests for missing risk-free, missing benchmark, and risk upstream failure posture.

Exit criteria:

1. risk sections never imply unsupported precision,
2. risk unavailable states are visible and explainable,
3. risk supportability is represented in the report contract, not only logs.

### Slice 6: Evidence Pack, Lineage, And Data Mesh Alignment

Goal: make every material report claim traceable.

Required work:

1. add report-level evidence refs for every sourced section,
2. emit source service, source entity id, source as-of date, generated-at, and correlation id,
3. align the `ClientReportEvidencePack` domain-product declaration with implemented routes,
4. update trust telemetry only when implementation evidence exists,
5. validate upstream consumer declarations for governed source products used by the report,
6. add evidence contract tests.

Exit criteria:

1. `make domain-product-validate` passes when declarations change,
2. every numeric section has source refs or section-level source refs,
3. customer-consumable evidence is separated from operator-only evidence.

### Slice 7: Advisor Discussion And Follow-Up

Goal: provide useful advisor preparation without creating actions in `lotus-report`.

Required work:

1. add deterministic discussion prompts grounded in report facts,
2. add route targets for Workbench drilldown,
3. reference proposal, risk, performance, and action-register surfaces only as navigational targets,
4. keep action creation outside `lotus-report`,
5. add advisor-only/client-ready separation tests.

Exit criteria:

1. prompts are factual, deterministic, and source-backed,
2. no prompt creates, approves, or mutates upstream actions,
3. advisor-only fields are clearly separated from client-ready fields.

### Slice 8: Gateway And Workbench Consumption Readiness

Goal: prepare the report for front-office presentation after the core contract is stable.

Required work:

1. expose the report through `lotus-gateway` only if the contract affects Workbench,
2. add gateway contract tests for partial/unavailable states,
3. add Workbench report preview only after the `lotus-report` contract is stable,
4. validate canonical front-office runtime only when Workbench product flow changes,
5. preserve `PB_SG_GLOBAL_BAL_001` contract provenance when canonical demo evidence is needed.

Exit criteria:

1. gateway consumers do not depend on undocumented fields,
2. Workbench does not render speculative or advisor-only material as client-ready,
3. cross-repo validation evidence is recorded if gateway or Workbench changes are made.

### Slice 9: Supported Features And Capability Publication

Goal: make product truth explicit and implementation-backed.

Required work:

1. update `docs/supported-features.md` as each RFC-0002 feature is implemented,
2. update `GET /integration/capabilities` only for implementation-backed features,
3. keep legacy `lotus-report.reporting.portfolio_review` semantics clear until replaced or
   deprecated,
4. update traceability docs with evidence pointers for new feature keys,
5. avoid product wording that claims planned behavior is shipped.

Exit criteria:

1. supported-features rows link to tests, API surfaces, and docs,
2. capability keys match actual behavior,
3. planned RFC features are clearly separated from implementation-backed product material.

### Slice 10: Hardening And Review

This is the second-last slice. No new feature scope should be added here.

Goal: perform a full implementation review and close quality gaps before final documentation and
branch closure.

Required work:

1. perform a proper code review of the full implementation,
2. tighten loose ends in contracts, naming, tests, docs, and runtime behavior,
3. check API certification pattern compliance,
4. verify OpenAPI quality, vocabulary, no-alias governance, examples, and error posture,
5. verify platform governance and data mesh enterprise standards requirements are met,
6. review `ClientReportEvidencePack` declaration and telemetry alignment,
7. review test quality for behavior coverage rather than superficial schema snapshots,
8. make final quality improvements before closure.

Exit criteria:

1. all review findings are fixed, explicitly deferred with rationale, or converted to tracked
   follow-up issues,
2. `make check` passes,
3. `make domain-product-validate` passes when data product contracts are touched,
4. `make ci` or the documented PR-grade equivalent passes before merge,
5. GitHub PR checks are green or any non-blocking/manual tier is explicitly classified.

### Slice 11: Closure, Documentation, Context, Wiki, And Branch Hygiene

This is the final slice. It closes the RFC rollout and must not add new product behavior.

Required work:

1. update documentation,
2. update agent context when repository truth, commands, integration posture, or repeatable guidance
   changed,
3. update wiki source and publish the wiki after merge,
4. update supported-features material so delivered features are implementation-backed product truth,
5. update RFC traceability and any release notes required by the repo,
6. perform branch hygiene and cleanup,
7. make a conscious review of whether skills, guidance, documentation, or agent context should be
   improved for better future work, faster ramp-up, and stronger agent effectiveness,
8. identify what should be added, removed, tightened, or clarified; if no changes are needed, state
   that explicitly as a deliberate outcome.

Closure decisions:

1. Repository documentation and context are updated to describe the shipped first-class review
   contract rather than the pre-RFC generic aggregation posture.
2. `docs/supported-features.md` remains the durable supported-features source and already contains
   only implementation-backed RFC-0002 feature rows; no aspirational RFC-0002 rows remain.
3. Repo-local wiki source is updated only with concise operator-facing semantics and request
   examples; the full implementation plan remains in this RFC to avoid duplicate documentation.
4. Repository agent context is updated because the first-class review contract changes
   `lotus-report` current-state truth and future ramp-up expectations.
5. Central platform context and skills do not require changes from this RFC. The existing backend
   delivery, endpoint certification, PR pre-merge, and wiki publication guidance already covered
   the workflow; no new repeatable cross-repo pattern emerged that should be promoted into a skill.
6. Post-merge wiki publication remains an explicit operational step:
   `lotus-platform/automation/Sync-RepoWikis.ps1 -Publish -Repository lotus-report`.
7. Final gold-standard review after Slice 11 identified one implementation gap: the contract
   allowed `not_applicable` section states but the service did not emit them. The closure hardening
   pass now marks requested supporting sections with no applicable income/activity, holdings, or
   transactions as `not_applicable` with source-backed evidence.
8. A follow-up runtime proof review identified an operational gap in local Docker Compose: the
   container could expose `report.dev.lotus` to callers while failing to reach host-published
   canonical upstream ports. The closure hardening pass now configures Compose with explicit
   host-reachable upstream URLs and a regression test so canonical front-office proof does not
   require ad hoc container replacement.
9. A private-banking quality review identified additional report-value gaps: the report needed a
   proper client and mandate frame, clearer meeting organization, explicit advisor checks, and
   governed AI posture. The hardening pass now sources client/advisor/mandate fields from
   `lotus-core /portfolios/{portfolio_id}`, adds deterministic `reportStructure` and
   `advisorBriefing`, and exposes `aiReadiness` as guardrail metadata rather than generated advice.

Exit criteria:

1. final docs describe shipped behavior, not planned behavior,
2. supported-features material matches code and tests,
3. repo-local wiki source has no drift against intended published wiki content,
4. post-merge wiki publication is completed,
5. feature branch is merged or intentionally left open with a documented reason,
6. stale local and remote branches are cleaned up after merge,
7. final PR evidence lists the exact commands run and their results.

## Acceptance Criteria

The RFC implementation is complete only when all criteria below are true:

1. `POST /reports/portfolios/{portfolio_id}/review` returns a typed
   `PortfolioReviewReportResponse`.
2. The route has ready, partial, unavailable, omitted-by-request, and not-applicable section states.
3. Client-ready and advisor-only content are separated in the contract and tests.
4. Performance sections identify period, benchmark, fee treatment, gross/net basis, annualization
   posture, methodology, and source freshness when applicable.
5. Risk sections identify risk source, supportability, unavailable reasons, and methodology context.
6. Evidence refs exist for every sourced report section.
7. `ClientReportEvidencePack` declaration and trust telemetry match actual implementation, or the
   RFC explicitly leaves them unchanged with rationale.
8. `GET /integration/capabilities` and `docs/supported-features.md` do not claim planned features as
   implementation-backed.
9. OpenAPI, vocabulary, alias, and API certification expectations are satisfied.
10. Documentation, wiki, and repository context reflect shipped behavior.
11. GitHub checks run asynchronously on the remote branch or PR and are monitored until green,
    failed with fix-forward work, or explicitly classified as non-blocking/manual.
12. No direct commits land on `main`; all work lands through a feature branch and PR.

## Validation Plan

Targeted local proof:

1. `git diff --check` for documentation-only slices,
2. `make check`,
3. `make domain-product-validate` when data product declarations or telemetry change,
4. targeted unit tests for `ReportingReadService`,
5. targeted integration tests for `/reports/portfolios/{portfolio_id}/review`,
6. OpenAPI generation/validation checks included in the repo-native lane,
7. `lotus-platform/automation/Sync-RepoWikis.ps1 -CheckOnly -Repository lotus-report` when
   repo-local wiki source changes.

PR-grade proof:

1. `make ci`,
2. Docker validation when route/runtime dependencies change,
3. gateway validation if a gateway-facing contract is added or reshaped,
4. platform canonical runtime validation only when Workbench product flow changes,
5. GitHub PR checks monitored with regular intervals while pending.

Suggested monitoring cadence:

1. check GitHub run status immediately after push,
2. while checks are pending, re-check every 10 to 20 minutes or when GitHub posts a failure,
3. diagnose failing jobs from logs,
4. fix-forward on the same branch,
5. rerun the affected local gate before pushing the fix.

## Branching And Delivery Expectations

Delivery rules for this RFC:

1. use one feature branch for RFC hardening and follow-on implementation unless the work becomes too
   broad and needs smaller PRs,
2. do not commit directly to `main`,
3. push the feature branch to `origin` so GitHub can run checks,
4. open a draft PR when implementation starts or when asynchronous GitHub checks are needed for
   review evidence,
5. keep commits small, meaningful, and scoped to one slice or documentation concern,
6. do not let CI health drift; fix failed required checks promptly,
7. do not merge on red checks,
8. after merge, delete the remote feature branch and local feature branch, then sync `main`.

For the RFC-hardening slice, the intended branch is:

```text
feature/rfc-0002-portfolio-review-report
```

## Risks And Mitigations

1. A rich report contract can become too large.
   Mitigation: keep first-paint report sections compact and push tables into appendices.
2. Client-ready and advisor-only content can be mixed accidentally.
   Mitigation: enforce separate sections and tests.
3. Reporting can drift into domain authority.
   Mitigation: every calculated field must identify upstream source or documented report-only
   derivation.
4. Performance and risk semantics may be jurisdiction-sensitive.
   Mitigation: label methodology and disclosures explicitly; require compliance review before
   client production use.
5. Existing mixed request aliases can confuse consumers.
   Mitigation: keep compatibility only where tested, document canonical snake_case request fields,
   and provide examples in wiki/API docs.
6. Data mesh declarations can overstate implementation maturity.
   Mitigation: update `ClientReportEvidencePack` declaration and telemetry only when route behavior,
   tests, and docs match.
7. Advisor prompts can become speculative recommendations.
   Mitigation: keep prompts deterministic, source-backed, and advisor-only unless separately
   reviewed for client presentation.
8. Front-office latency can degrade meeting experience.
   Mitigation: measure route latency and introduce prepared-report/cache design if live composition
   is not acceptable.
9. Documentation can sprawl across RFC, docs, and wiki.
   Mitigation: keep RFC decisions in `rfcs/`, implementation evidence in `docs/` and tests, and
   concise operator usage in `wiki/`.

## Open Questions And Slice Decisions

Resolved during implementation:

1. Slice 2: The canonical first implementation continues deriving standard review periods from
   `as_of_date`; no `review_period` request field is introduced yet.
2. Slice 6: `ClientReportEvidencePack` evidence is returned inline in the review response for the
   first implementation. A dedicated evidence-pack route remains out of scope until a consumer needs
   independent evidence retrieval.
3. Slice 3/Slice 7: Target/model allocation references remain outside `lotus-report` until an
   upstream owner publishes a governed source. The report may link to proposal/action surfaces but
   must not invent target allocation truth.
4. Slice 7: Advisor discussion prompts are deterministic, rule-based, source-backed, and
   advisor-only. AI-generated wording remains outside `lotus-report`; the report now publishes
   deterministic `aiReadiness` metadata that names supported grounded-assistance use cases and
   blocks trade recommendations, suitability determinations, and client-profile inference until
   governed `lotus-ai` integration exists.
5. Slice 4/Slice 5: The first implementation publishes methodology and supportability metadata but
   does not ship jurisdiction-specific disclosure packs.
6. Slice 8: Gateway preservation is the first consumer readiness proof. Workbench report preview is
   intentionally deferred until product rendering scope is explicitly accepted.

## Success Criteria

The first implementation is successful when an advisor can use the endpoint to prepare or conduct a
portfolio review meeting and answer:

1. what is the portfolio worth,
2. how is it allocated,
3. how did it perform versus benchmark,
4. what drove performance,
5. what risks or concentrations need attention,
6. what income, cash, and activity changed,
7. what holdings and transactions support the discussion,
8. what follow-up should be considered,
9. which figures are sourced, fresh, partial, or unavailable,
10. what disclosures and methodology apply.

The implementation is not successful if it only returns more data. It must return a report contract
that is supportable, traceable, audience-aware, and front-office usable.
