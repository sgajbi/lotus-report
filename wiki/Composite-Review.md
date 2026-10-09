# Composite Review

Report #417 introduces an internal composite calculated review on the existing
report lifecycle. The catalogue admits JSON/XLSX; XLSX readiness requires actual
runtime and exact template-version/type/contract/format evidence. Full #417 and
RPT-01–12 acceptance remain open, including uncaptured supplier products and
institutional authority. Excel availability does not grant official publication.
See the [API guide](https://github.com/sgajbi/lotus-report/blob/main/docs/composite-report-api-guide.md)
for intake, retained reads, refusal states and executable proof boundaries.

## Exact selection and retained evidence

`POST /reports/composite-reviews` requires caller tenant/actor/application/region
context and `Idempotency-Key`. Its `selection` names tenant, composite, calculation,
currency, fee view, methodology, engine/fingerprint, response digest and an ordered
vector of 1–120 contiguous retained windows. Each window pins materialization,
period, sequence, definition/membership/attestation hashes, source cut, method
binding and retained receipt. No latest selection or implicit source refresh exists.

The existing worker calls Performance's explicit-vector `POST /composites/twr`.
Mismatch, missing period/member, nonfinite financial evidence or incompatible
context fails capture. Successful capture retains the complete source response,
canonical cells and pointers, immutable hash and Report revision. Existing job
and snapshot APIs enforce tenant isolation. A six-year fixture proves no
presentation-side five-year truncation; the supplier's 120-window limit is explicit.

## Semantic and authority boundary

The versioned [schema](https://github.com/sgajbi/lotus-report/blob/main/contracts/composite_review.v1.schema.json)
and [semantic dictionary](https://github.com/sgajbi/lotus-report/blob/main/docs/composite-report-semantic-contract.md)
state exact source text, financial units, display conversion, rounding and
availability. Source ratio `0.01` means +1.00%; contribution `0.025` means 2.50
percentage points. Report never computes either value. Literal identifiers and
canonical companions must survive workbook creation without formulas or precision loss.

Summary, monthly or partial-period returns, contributions, methods, lineage and
disclosures are source-backed. Optional exact calendar/trailing TWR selections
use `composite_review.v2` and the `composite-review/v2` template. Calendar rows
populate `AnnualReturns`; `TrailingReturns` exists only when selected. Absent
products preserve v1 request/identity and unavailable states. Actual compatible
Render runtime/template/contract/format evidence is required for v2 XLSX orders.
Annual member dispersion remains uncaptured after a source policy-basis refusal.
Risk, complete member universe,
eligibility reasons/history, attribution and restatement remain explicit uncaptured
products. Qualification is `EXPLICIT_RETAINED_CALCULATED_REPLAY`, publication state
`NOT_ATTESTED`; the authority receipt/control revision are unavailable.

## Workbook custody and acceptance

The R5 v2 phase independently qualifies actual calendar/trailing workbook custody
using Report PR #423, Render PR #345 and Archive PR #183. Registered Report ASGI
routes and real PostgreSQL capture six accepted recorded Performance responses;
the existing actual Render/Archive HTTP clients archive original, financial
correction and retained-original rerender. Rerender makes no source request.
Each downloaded workbook reconciles 24,623 canonical/display cells, 8,571 financial
cells, 81 policies and complete pinned source products/identity/context.
Root independently accepts full bytes, tenant refusal and current financial
chain before and after Archive's same-PG/new-HTTP-process restart. Fresh-process
PostgreSQL-enforced read-only reopening preserves both jobs/snapshots, all six
upstream call records and pinned replay packages. Report's final consistent
backup is validated before exact owned PG/container/volume cleanup.

This remains calculated `NOT_ATTESTED` proof with recorded Performance transport,
explicit isolated Archive adapters and disclosed controlled degraded health.
The Archive financial-chain QA control is separate from Report correction
orchestration and institutional approval. Annual dispersion, since-inception,
complete membership history and full #417/RPT-01–12 acceptance remain open.
Exact source revisions, manifests, native receipts and proof limits are in the
[R5 delivery ledger](https://github.com/sgajbi/lotus-report/blob/main/docs/composite-report-delivery-ledger.md#current-v2-evidence-r5-actual-http-custody).

The historical v1 R4 evidence remains immutable:
One controlled six-year vector (January 2020–December 2025, 72 windows and 2016
member rows) is independently qualified through registered Report PostgreSQL
capture, actual Render/Archive HTTP, retained-original rerender and Archive
restart. Each of the three actual workbooks reconciles all 24,604 semantic cells
across 43 sheets, including complete contribution/evidence partitions and
lossless identity fragments. Original and corrected source cumulative returns
remain exact; version-specific pins and unavailable reasons are preserved.
Root independently checks custody, tenant refusal and current financial-chain
resolution before and after restart; all owned phase resources are cleaned.
See the [delivery ledger](https://github.com/sgajbi/lotus-report/blob/main/docs/composite-report-delivery-ledger.md) and
[bounded issue proof](https://github.com/sgajbi/lotus-report/issues/417#issuecomment-6075090685)
for revisions and evidence. This is controlled calculated NOT_ATTESTED evidence,
not institutional authority or enterprise capacity. Full #417 acceptance,
uncaptured source products and the other RPT families remain open.

The workbook composer reads the persisted snapshot record and checks its revision
binding. Archive metadata has `portfolio_scope=composite`, null `portfolio_id`,
the real `composite_id` and `composite_report_identity` carrying exact selection
and three lifecycle digests. It preserves revision and document references.

SQLite and isolated PostgreSQL API/worker/process-retention proofs include an
actual registered Performance response from merged main
`c100c885752c86b8d950d7970c99a8d223e6376a`. The captured producer uses controlled
economic/provider/authority inputs; Report replays the exact captured transport.
Regression tests preserve available `0E-12` dispersion and external source
identity, and refuse fee mismatch or changed financial content. The registered
XLSX package tests use normal shipped admission with a controlled Render boundary;
actual supplier completion is separately qualified in the delivery ledger.
Archive custody and technical rerender preserve the original snapshot, template
and output format. Financial correction requires a fresh exact-pinned order/key;
generic regenerate/replay remain portfolio-only. Manage #714,
Performance #540/#610, Gateway #820 and institutional authority remain owner dependencies.

Methods rows use sorted method-binding keys so JSON object key order, including
PostgreSQL JSONB normalization, does not change row identity or source pointers.
The retained source values remain exact. Previously captured snapshots keep their
stored tables, and technical rerender continues to consume those immutable tables.

## Linked contribution primary (v3)

The existing order endpoint also accepts an exclusive typed `linked_selection`.
It captures the exact source-owned linked member analysis through
`POST /composites/analytics`, independently of the TWR primary and return products.
Its accepted data/input contract is `composite_review.v3`; XLSX requires ready
`composite-review/v3` support before capture. JSON has no Render dependency.
Seven source-bound tables preserve complete member/period rows, canonical decimals,
methods, nested source authority and explicit uncaptured products. Report validates
the whole projection, with no investment linking or residual allocation.

The original/corrected fixture comes from accepted Performance main `6e9bdbb` with
controlled synthetic inputs and real supplier PostgreSQL. Report's tests replay
that transport; publication remains `NOT_ATTESTED`. New correction orders keep
distinct retained identities. Current generated v1/v2 scalar schemas explicitly
accept genuine finite scientific decimal strings, while frozen historical schemas
and retained source values remain unchanged. See the
[semantic contract](../docs/composite-report-semantic-contract.md#linked-member-analysis-composite_reviewv3)
and [API guide](../docs/composite-report-api-guide.md#exact-linked-contribution-primary).
This advances bounded RPT-01/RPT-06 evidence; full #417 and the twelve products remain open.
