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
| `evidence` | source refs, lineage bundle, trust metadata, and domain-product context. Trust claims state only what is proven: `evidence_posture` separates the synchronous `ephemeral_composition` from durable `durable_snapshot` capture; `tenant_id` appears only when `tenant_admission` establishes it (never a fabricated default); `reconciliation_status` stays `unknown` with a bounded reason until an explicit policy proves reconciliation |

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
- **Pre-order availability**: `GET
  /integration/report-ordering-catalogue/advisor-commentary-availability` answers, per
  portfolio and context, whether an accepted brief exists BEFORE the order is placed, via the
  lotus-ai latest-accepted lookup (lotus-ai#183). `ready` returns the accepted run id (the
  value to submit as `options.advisor_brief_run_id`), reviewer identity, and content hash;
  `unavailable` distinguishes `advisor_brief_not_reviewed` (no accepted brief for the
  portfolio) from `advisor_brief_context_mismatch` (accepted briefs exist, none assert the
  requested date/currency) and `advisor_brief_availability_unknown` (the lookup could not
  answer - not proof of absence). Ordering surfaces (Gateway/Workbench) compose this into
  the section's availability state.
- **PDF orders are supported.** The lotus-render template draws the section, including the
  per-claim grounding marker, so a governed PDF shows which AI-drafted claims a reader can
  check (lotus-render#218/#226). The temporary refusal gate that stood while that was untrue
  has been removed; it was held for the grounding marker specifically, not merely for a
  template, because an unverifiable claim that looks verifiable becomes durable evidence once
  a PDF is archived.
- A lotus-ai 401/403 (the `lotus-report` caller missing or inactive in the lotus-ai
  access-control registry) fails the capture retryable and is an environment fault to fix in
  lotus-ai, not a section posture.
- At capture time the accepted output is resolved by run id from lotus-ai (recorded as durable
  upstream-call lineage like every other source read). The exact reviewed narrative is composed
  unmodified - lotus-report never regenerates, edits, or re-reviews AI content.
- The pre-order availability check and the capture resolve these reasons through one shared
  vocabulary (`app/advisor_brief_source_reasons.py`), so the answer an operator gets before
  ordering and the reason recorded on the job after cannot disagree about the same fact.
- The section **fails closed without failing the report** on definitive postures, with one
  bounded reason recorded on the job (`job_advisor_commentary_unavailable` event, snapshot
  package, and lineage summary): `advisor_brief_not_reviewed` (run not completed/accepted, superseded, or
  no accepted brief exists for the portfolio at all - in every case the operator
  reviews and accepts a brief, and no retry changes the answer),
  `advisor_brief_not_found` (unknown run or unretrievable output),
  `advisor_brief_not_validated` (the brief exists and was found, but lotus-ai withheld it
  because its deterministic output validation never returned VALIDATED - the operator action is
  to re-run the brief so it acquires a verdict, not to look for a missing run),
  `advisor_brief_source_unproven` (lotus-ai could not prove which run answers the request, for
  example a saturated candidate scan; the brief likely exists, and the condition clears through
  an operator action - a narrower report context, or a widened bound in lotus-ai - so a later
  order succeeds. This capture does **not** retry it: an identical request saturates an identical
  bound, and failing the job would deny the client a report over one optional section),
  `advisor_brief_source_refused` (lotus-ai refused for a reason lotus-report does not recognise -
  a reason report cannot interpret is not evidence of a cause it can name, so this never
  masquerades as a known posture), `advisor_brief_source_contract_violation` (lotus-ai answered
  **200** with a payload that breaks its own published contract: a projection naming a different
  run, schema, or tenant than the one requested, or a validation verdict that is absent, partial,
  or not VALIDATED when the contract makes it always present and complete. Distinct from
  `advisor_brief_not_validated` on purpose - that is lotus-ai correctly *withholding* a brief,
  this is lotus-ai *publishing* something it promised to refuse. The operator actions are
  opposite: re-run the brief, versus investigate lotus-ai because a guarantee it publishes has
  regressed. The recorded `detail` names the specific field), `advisor_brief_context_mismatch` (the brief asserts a
  portfolio, period, as-of date, reporting currency, or benchmark that differs from the
  report's; nulls mean "not asserted" and never conflict. lotus-ai's own `no_context_match`
  refusal - accepted briefs exist, none assert the requested date or currency - records the
  same reason, because it is the same fact established one step earlier. The operator corrects
  the report context, and must not be sent to widen a scan bound), and
  `ai_disclosure_policy_unavailable` (accepting reviewer identity, content hash, or grounding
  source refs missing, so the mandated disclosure line cannot be rendered truthfully).
- Transport-level lotus-ai unavailability fails the capture with the standard retryable
  posture instead - retrying can succeed, so the pack does not silently ship without a section
  the caller ordered.
- When included, the render package and JSON output carry the summary, talking points, risks
  and exceptions, the review identity, the pinned `content_hash`, and the AI-assistance
  disclosure line; archive metadata keeps the run id, request id, reviewer, review time, and
  content hash. Each narrative item's `evidence_refs` carries lotus-ai's typed grounding shape
  (`{metric_label, metric_value, source_ref}`, all required - lotus-ai#189); incomplete or
  differently shaped entries are dropped rather than composed as partial grounding.
- Each narrative item also carries a **`grounding`** posture - `grounded` or `ungrounded` -
  stating whether the claim reached the page with evidence a reader can check. It is stated
  rather than left to be inferred from an empty `evidence_refs` list, because an ungrounded
  claim is otherwise distinguishable only by contrast with grounded claims on the same page,
  and that signal disappears precisely when no claim is grounded. An ungrounded item is drawn
  neutrally, not as a warning: a named reviewer accepted it, so the page says what is and is
  not checkable without editorialising, and the claim is never silently dropped.
  When refs were supplied but none survived the shape check, the item additionally carries
  **`unusable_evidence_ref_count`**. Both cases read identically to a client - not checkable
  either way - but an operator needs the difference between "cited nothing" and "cited
  unreadably".
- lotus-report calls lotus-ai as registered caller `lotus-report` (`X-Caller-App`); that
  caller must be registered and active in the lotus-ai access-control registry for the
  environment.

## Render package semantics (portfolio review)

The render package carries typed semantic blocks beside the raw section data. Each states a
reporting judgement Render must read rather than re-derive; the shared rules are:

- **Posture is authoritative, never inferred.** `ready` / `empty` / `unavailable` are different
  claims: *empty* is a fact about the portfolio and is drawn; *unavailable* is a fact about the
  data and is said. A consumer must never infer meaning from an empty list or a present key.
- **A subset never implies completeness.** Truncated or top-N sets carry reconciliation counts
  (`presented` / `available`, plus the covered share where provable).
- **A floor is not a total.** Sums over truncated evidence carry a completeness posture and the
  reviewed/source counts; the page states "at least X", never a total.
- **Absent is absent.** A basis, weight, or figure the capture did not establish is published as
  absent - never defaulted, never rendered as zero. A genuine zero is kept, because "nothing"
  is a finding and "unknown" is not.
- **Codes are forwarded verbatim.** Note `code`/`severity` values are the operator's join key;
  Report never rewords an unfamiliar one into a guess.

Blocks, with their one load-bearing rule each:

| Block | States | Load-bearing rule |
|---|---|---|
| `allocation_presentation` (#224) | The resolved, ordered dimension set with per-dimension posture | Render draws the resolved list, never guesses from which `by_*` keys have rows |
| `contribution_ranking` (#209/#228) | Ranked contributors, both signs, with `presented_/available_count`, `presented_contribution_pct`, `unexplained_residual_pct`, `unusable_row_count` | The reconciliation describes exactly the presented set; a one-sided page means a genuinely one-sided period |
| `risk_posture` (#234) | Why a risk figure is missing: posture + notes (+ `affected_measures`) | `missing_benchmark` (mandate fact) and `risk_upstream_failure` (transient) must not produce the same page |
| `risk_methodology` (#235) | VaR method/confidence/horizon, `return_basis` | A tail-risk number without its basis is not interpretable; absent basis is published as absent |
| `benchmark_presentation` (#241) | `available` / `unavailable` / `not_requested` + benchmark identity | A failed comparison must not render as an unbenchmarked mandate; replayed captures resolve from the ORDER, not table values |
| `performance_basis` (#243/#247) | `return_basis: NET`, plus signed `fee_drag.gross_minus_net_pp` | Fee drag is computed from raw returns, never from displayed (rounded) numbers; sign preserved |
| `holdings_presentation` (#245) | Posture, `presented_/available_count`, `presented_weight_pct`, Core's `supportability_status` verbatim | Empty portfolio != unavailable holdings != unreconciled holdings != trusted-complete - four distinct states |
| `attribution_bridge` (#254) | Brinson bridge: effects with the hierarchy slot (`grouping_dimension`+`level`), source totals, reconciliation with source-classified residual, `ready`/`pending`/`unavailable` | The residual is presented, never allocated away; totals are the source's authoritative fields, never summed from rows; a pending async calculation is said with its identity, never waited on |
| `earnings_statement` (#249) | Income gross->withholding->net (+ by-type), realized P&L with named largest gain/loss, `completeness` | `window_truncated` sums are a floor: the page says "at least X, based on N transactions reviewed" and never the word "total"; truncated zeros never claim an empty period |

Render-side drawing contracts are agreed per block before either side builds (recorded on the
linked issues); the package is additive, so undrawn blocks change nothing on the page.

## Performance attribution section (PERFORMANCE_ATTRIBUTION)

Benchmark-relative return attribution — allocation, selection, and interaction effects by
asset class — answering "why did we outperform?". Sourced from lotus-performance's stateful
Brinson calculation (`/performance/attribution`, model `brinson_fachler`, linking `carino`);
the effects, authoritative level totals, reconciliation and source-classified residual are
composed **verbatim**. Report never rebalances a residual or reweights an effect, and the
page draws the residual as its own labelled bridge segment.

- **Opt-in** (`default_selected=false`) while the asynchronous capture proves itself in
  production: the first section whose source may answer *accepted-but-not-complete*, and
  default selection would add a submit-and-poll to every order's capture latency before that
  posture has real-order evidence. The flip to default-on is a later, evidenced decision.
- **Benchmark defaulting**: an order without `benchmark_code` follows the catalogue's
  recorded policy — the calculation runs against the **portfolio's assigned benchmark**
  (lotus-performance resolves it from the lotus-core assignment; the omission is passed
  through as an omission, never as an empty string). The page names the benchmark the source
  actually computed against; the requested code stays in lineage.
- **Calculation identity**: the caller-supplied `calculation_id` is derived from the
  canonical serialization of the request body — *same financial question, same identity* —
  so an identical capture retry or a regenerate of the same question **converges on the same
  upstream calculation** (lotus-performance REPLAYs a matching resubmission). A different
  benchmark, window, grouping or basis is a different question with its own identity.
- The section **fails closed without failing the report**, with one bounded reason:
  - `attribution_accepted_not_complete` — the source accepted the calculation and had not
    finished it within the capture's poll budget (which honours the source's stated
    Retry-After in full, or not at all). Not a failure: **regenerating the report collects
    the finished result** under the same calculation identity.
  - `attribution_execution_failed` — the calculation ran at the source and failed, or the
    identity conflicted; the source's own detail text (forwarded verbatim) says which.
    **Re-ordering the report is NOT the remedy**: the failed execution is held by source
    idempotency, so a regenerate converges on the same failure. Recovery of failed compute
    jobs is lotus-performance's operator recovery; regenerate after it.
  - `attribution_unsupported_for_portfolio` — the source cannot support the calculation for
    this portfolio's inputs (e.g. no benchmark assignment). A fact about the mandate's data.
  - `attribution_source_refused` — a refusal Report does not recognise, said as such.
  - `attribution_upstream_failure` — lotus-performance unreachable for this capture.
- Every capture outcome is recorded to metrics (`attribution_capture`: `ready` / `accepted`
  / `unavailable`, failure category = the section's own reason code), so a dashboard tells
  "still computing" from "refused" without reading job records.
