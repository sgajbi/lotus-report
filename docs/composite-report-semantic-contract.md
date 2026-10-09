# Composite Review Semantic Contract

Monthly source amendments use additive `composite_review.v6`, documented in
[Monthly source-amendment capture and XLSX packaging](composite-monthly-source-amendment.md).
Source-v2 and definition-v1/v2 are independent axes. V1–v5 meanings remain frozen;
amended eligibility is never joined to previously captured Performance aggregates.

Draft shared version: `composite_review.v1`, under Report #417 and Render #338.
The executable schema is [the shared JSON schema](../contracts/composite_review.v1.schema.json).
The [synthetic example](../contracts/examples/composite-review.v1.json) binds to
`tests/unit/composite_reporting/`: +1% in January and +3.02% through February.
These literal source fixtures establish consumer admission/echo behavior, not
actual Performance producer, official publication, Excel or Archive acceptance.

## Handoff And Custody

Reuse `render_package.v1`, with `report_type=composite_review`,
`report_data_contract_version=composite_review.v1`, `template_id=composite-review`,
`template_version=v1`, `output_format=xlsx`. Existing job, snapshot, render,
revision and Archive identity contracts remain the lifecycle authority.
Report does not render or submit a document to Archive for creation.

The workbook composer requires the persisted snapshot record, not just a
caller-supplied payload. It verifies job/tenant/composite/horizon and the canonical
revision binding. `render_context.archive` states `portfolio_scope=composite`,
`portfolio_id=null`, the real `composite_id`, and `composite_report_identity` with
contract version, qualification, publication state, exact selection, and bare
64-character `series_digest`, `source_revision_digest`, `factual_content_digest`.
This is the Archive #176 candidate custody contract. Existing revision/document
reference and Render artifact identity remain authoritative.

`report_data` retains the unchanged Performance `source_response`, its
Report-canonical `source_response_digest`, exact accepted `selection`,
Report-owned `report_facts`, and semantic `tables`. Selection includes tenant,
composite, calculation, inclusive period, fee/currency/method/engine identities,
calculation fingerprint and response digest. Each ordered window pins its
materialization, definition/membership/universe hashes, source cut, method
binding, receipt fingerprint and fact sequence. There is no latest fallback.

Performance PR #630 merged at
`c100c885752c86b8d950d7970c99a8d223e6376a`. The producer limit is 120 exact
windows, with larger selections refused rather than truncated. Its explicit
selection is calculated replay, not official/freeze/control authority.

The actual registered PostgreSQL producer response at that revision is retained
in `tests/unit/composite_reporting/wire_fixtures/`, with intake hashes and explicit
controlled-input qualification. Consumer tests replay this captured transport
through Report's registered workflow, preserve available `0E-12` dispersion and
external source identity, and refuse changed fee identity or corrupted values.
This establishes wire compatibility; the producer's economic/provider/authority
inputs remain controlled and no live network or institutional authority is claimed.

The same fixture directory retains a separately produced matched original and
financially corrected pair from that committed Performance revision: cumulative
returns `0.030200000000` and `0.055700000000`. Registered consumer tests order each
exact selection independently for JSON and XLSX, preserve both captures after
correction and store reopen, and reuse each order's own idempotency key. February's
monthly evidence and pin remain unchanged; its cumulative return includes the
corrected January. Each accepted request retains its existing semantic identity,
source revision and report revision. XLSX unit tests explicitly decline rendering
at a controlled boundary and establish no workbook or Archive completion.
This test-evidence addition does not change the published wiki workflow.

The current `qualification` is `EXPLICIT_RETAINED_CALCULATED_REPLAY`, and
`publication_state` is `NOT_ATTESTED`. The typed authority slot under
`report_facts.authority` has null `receipt` and `control_revision`,
`availability=UNAVAILABLE`, `reason_code=SOURCE_AUTHORITY_NOT_ATTESTED`.
Non-null authority is refused by current admission. Performance #610 may supply
a future compatible authority extension; this contract does not invent its
issuer, grants or approval semantics.

## Financial Cell Dictionary

Columns declare type, source unit, display unit/conversion, decimal places,
currency and scale. Each cell carries exact canonical text or null, availability,
bounded reasons and a JSON pointer. Pointers resolve against the retained
`source_response`, `selection`, or explicitly Report-owned `report_facts`.
Report-generated financial cells must resolve to actual source financial fields;
Report-owned text/disclosure facts cannot masquerade as authoritative returns.
Admission permits only exact consumed financial paths: the response's
`cumulative_return`, declared period financial/count fields under
`/source_response/periods/<index>/`, and declared member financial fields under
`/source_response/periods/<index>/member_contributions/<index>/`. An additive
field named `return_value` outside those paths is refused, and a known financial
field cannot be relabelled TEXT to evade unit validation.
Every canonical cell must equal its pointed source value exactly. JSON integers
are represented as exact count text, and source decimal text keeps its spelling.

| Type | Source Unit | Display | Conversion | Currency / Scale |
| --- | --- | --- | --- | --- |
| DECIMAL_RETURN | DECIMAL_RATIO | PERCENT | RATIO_TO_PERCENT_DISPLAY | None / 1 |
| DECIMAL_RETURN contribution | DECIMAL_RATIO | PERCENTAGE_POINTS | RATIO_TO_PERCENT_DISPLAY | None / 1 |
| MONEY | CURRENCY_UNITS | CURRENCY_UNITS | IDENTITY | Selected ISO currency / 1 |
| COUNT | PORTFOLIO_COUNT | PORTFOLIO_COUNT | IDENTITY | None / 1 |
| TEXT | TEXT | TEXT | IDENTITY | None / 1 |

Source `0.010000000000` remains numeric ratio `0.01` under Excel percentage
format and displays **1.00%**, never 100%. Contribution `0.025` displays
**2.50 percentage points** with that explicit column label. Basis points are
not an admitted unit in this first contract; a caller cannot relabel a ratio as
basis points. Money is not scaled to thousands or millions implicitly.

`display_rounding_mode=HALF_UP` applies only to presentation. The
`RATIO_TO_PERCENT_DISPLAY` rule displays ratio times 100 exactly once, rounded
to the column's declared decimal places. Guarded Excel numeric storage retains
the ratio and uses percentage format; text fallback performs that same display
conversion once and retains the unchanged canonical companion. The conversion
name describes meaning rather than a mandatory storage representation. It does
not authorize changing a source return, double scaling, or dropping precision
from retained canonical text.

V1 and v2 admission checks the complete captured source projection as well as each
cell pointer. Omitting a financial member/period, coherently removing a column,
adding a copied member row, changing a label/precision/null reason or rewriting a
disclosure refuses with `COMPOSITE_REPORT_PROJECTION_CONFLICT`. These checks cover
the captured financial population, not an uncaptured expected eligibility universe.
The existing builders remain the canonical projection; no financial values are
recomputed. Valid dataset fields, wire contracts and stored bytes remain unchanged.
Historical Methods ordering remains valid only for a complete permutation of the
same source paths with exact retained ordinal row/pointer bindings. Validation
normalizes a temporary comparison copy and never rewrites retained evidence.

Render owns display conversion, formatting and declared partitions. Render
must retain an exact canonical text companion for each financial cell whenever
Excel's numeric precision cannot preserve the source value, and describe numeric
display precision separately. It must not change the authoritative canonical
value or introduce an investment-return formula. Text identifiers remain literal,
including leading zeros and formula-like text. Sheet names, limits, declared
partition counts, margins and accessibility are Render-owned consumer behavior.

## Availability And Report Scope

Available zero is distinct from null. UNAVAILABLE, UNKNOWN and NOT_APPLICABLE
require null canonical content and bounded reasons. Source failure, malformed
financial evidence, absent manifest, changed pins, incompatible context, missing
period/member, forged cell pointer/value or fabricated authority refuses.
Optional null metrics retain explicit unavailability rather than zero.

Initial source-backed tables: Summary, MonthlyReturns (only complete calendar
months; otherwise PeriodReturns), Contribution, Methods, Lineage, Disclosures.
AnnualReturns, Risk, Members, EligibilityReasons, MembershipHistory, Attribution
and Restatement have explicit uncaptured-evidence rows until their actual
supplier products are captured and reconciled. A ready financial fact count is
not an expected-universe count. A non-ready fact count is not a unique policy
exclusion count or a total of exclusion reasons. Nothing claims all RPT-01
content, RPT-01–12 completion, GIPS or official/institutional activation.

Rerender must use the retained immutable source and semantic tables with the
accepted template/contract. A changed authority or corrected source requires a
new candidate/version; it cannot rewrite the old snapshot. The catalogue's XLSX
readiness requires supplier runtime plus the exact template's type, contract and
output-format evidence. Global PDF readiness does not imply workbook support;
development template publication does not imply distribution authority.

Method-binding object keys are sorted before assigning Methods row identities
and escaped source pointers. JSON object insertion order, including PostgreSQL
JSONB key normalization, cannot change the semantic table for the same retained
values. Existing snapshots and their stored tables remain immutable; rerender
uses those tables rather than rebuilding them under a newer projection.

The current generic regenerate/replay command routes remain portfolio-review
only. Composite source correction requires a fresh composite order with explicit
corrected pins and a new retry identity; no command silently substitutes the
latest source. Workbook rerender retains the existing archived-job predicate,
original snapshot, accepted template and output format. The original job identity
already binds format; changing it requires a new job. The existing Archive client
records the old-to-new technical presentation relationship after verified custody.
## Captured return products: composite_review.v2

The v2 root retains v1 primary `selection`, `source_response` and
`source_response_digest` without adding fields to the supplier response. Its
bounded `source_products` array stores each typed `pin`, fixed `POST /composites/twr`
provenance, independent raw response and response digest. The v2 schema is
`contracts/composite_review.v2.schema.json`; the v1 example remains unchanged.
Current generated v1/v2 financial-scalar schema branches also accept finite
scientific decimal strings, including genuine retained `0E-12`. This explicit
`SourceNumber` metadata correction changes no native Decimal validation, retained
snapshot, historical schema packet or source value. JSON Schema's integer type
describes mathematical integers; native admission independently rejects Python
float inputs. Nonfinite values, booleans and malformed decimal text remain refused.

All thirteen legacy table identities remain present. `AnnualReturns` contains
calendar-product rows when selected, otherwise the exact legacy uncaptured
`metric/value/reason_code` row. `TrailingReturns` exists only when trailing
products were selected; there is no empty trailing placeholder. Source-backed
return tables have eleven ordered columns: `product`, `kind`, `period_start`,
`period_end`, `return_view`, `currency`, `return`, `status`, `methodology`,
`engine_version`, `response_digest`. Row identity is the product key, in request
order for that kind. Source indexes always refer to the original root array;
filtering calendar/trailing rows does not renumber pointers.

The sole additional financial path is
`/source_products/<canonical-index>/source_response/cumulative_return`, with
decimal ratio, percent display, two decimal places and HALF_UP rounding. Each
text column has an explicit selection/source/digest pointer. Cross-kind rows,
misleading labels, arbitrary financial basenames, period financial paths, TEXT
relabels and financial values placed in `report_facts` refuse validation. Complete
raw responses remain retained even when only their horizon return is presented.

V2 bounds match the consumer: 32 tables, 32 columns, 10,000 rows per logical table,
31-character table IDs, 256-character row IDs, 128-character column IDs,
256-character labels/titles and 32 reasons per cell. These do not replace the
renderer’s independent HTTP, expanded row/cell/sheet/text, output and execution
limits. Neither evidence nor precision may be dropped to fit a workbook.

## Linked member analysis: composite_review.v3

The same `composite_review` family accepts an exclusive `linked_selection` primary
operation. It is not a return-product selector and cannot be combined with TWR
`selection` or `source_products`. The caller pins the complete source request,
monthly selection windows, engine, calculation fingerprint and response digest.
The actual source request includes `restatement_sequence: null`; a numeric sequence
cannot compete with its explicit materialization IDs. The worker calls
`POST /composites/analytics` once and retains the complete response, including each
period's nested `source_authority_identity`.

The strict v3 root and typed source schema are in
`contracts/composite_review.v3.schema.json`. Seven logical tables are complete
source projections: Summary, LinkedContribution, LinkedPeriods, Methods, Lineage,
Disclosures and UncapturedProducts. Validation reconstructs the complete expected
projection from the retained source. Missing, extra or duplicate tables, rows,
columns, pointers, values or display policies refuse admission even when the
surviving pointers are valid. Member counts, period coverage and each member's
restatement identity must agree with the selected vector. These are identity and
completeness checks; Report does no investment calculation.

Canonical decimal text remains exact. Returns and weights display as percent;
contributions display as percentage points, each with two decimal places.
`DECIMAL_FACTOR` uses identity decimal-ratio display at twelve places. Source
reconciliation and display differences use percentage points at twelve places;
money uses source currency at two places; `COUNT` uses `PERIOD_COUNT` with zero
places. HALF_UP applies only to presentation. Source totals and differences are
never recomputed or allocated by Report.

Calendar, trailing, since-inception, risk, attribution, approved restatement and
complete eligibility population remain explicitly uncaptured in this standalone
analysis. Their null cells carry `SOURCE_PRODUCT_NOT_CAPTURED`. A missing source
calculation ID carries `SOURCE_IDENTITY_NOT_PROVIDED`. Qualification stays
`CALCULATED_ANALYSIS` / `RETAINED_SOURCE_ATTESTATION_NOT_LIVE_QUALIFIED`, with Report
publication `NOT_ATTESTED`. The v1/v2 accepted lifecycle and identities remain valid;
v3 is a bounded RPT-01/RPT-06 increment, not full #417 acceptance.

## Monthly eligibility and original history: composite_review.v4

The same endpoint accepts an exclusive `eligibility_selection`, with exact tenant,
composite, definition, currency, horizon and ordered monthly pins. It cannot be
combined with TWR, linked analysis or return products. The new dataset contract is
`composite_review.v4`, template `composite-review/v4`, consumer layout
`composite_workbook.v4`. Earlier schemas and serialization stay unchanged. JSON
uses the existing capture/job/snapshot lifecycle; XLSX requires actual exact v4
Render capability. Consumer deployment and joint runtime acceptance are separate.

`PUBLISHED` reads the whole monthly publication receipt plus exact canonical
membership, published universe, parent membership and publication. Its resolver
binding comes from the published universe's `CompositeMonthlyEvaluationApproval`
locator and content hash, never `claims_digest`. `EVALUATED_ONLY` reads the exact
proposal, complete expected universe, observations and all assessments. Approval,
publication and history are unavailable for that variant. Missing expected
observations remain source UNKNOWN assessments and PENDING_REVIEW outcomes.
Proposals and receipts cannot substitute for one another.

Eight ordered tables are Summary, Members, EligibilityAssessments,
EligibilityReasons, MembershipHistory, Methods, Lineage and Disclosures. Every
expected member and all three source rules are retained. Every failure and unknown
reason occurrence retains its exact ordinal/pointer. Membership's single
`reason_code` is only the first reason. Source unique excluded member counts differ
from reason occurrences. Known zero reason occurrences use a month-bound
NOT_APPLICABLE row with `NO_APPLICABLE_REASONS`. Rule-defined unused numeric nulls
(READINESS numeric slots and CASH gross-flow/event slots) use
`RULE_FIELD_NOT_APPLICABLE`; genuinely missing evidence remains UNAVAILABLE.
Nothing is silently converted to zero.

Whole raw values, decimal spelling, explicit nulls, booleans, nested authority and
locator hashes remain retained. Ratios use identity DECIMAL_RATIO display at twelve
places, money uses selected currency at two, portfolio/event counts have distinct
units at zero places, and booleans use lowercase text. HALF_UP is presentation only.
Report does not re-evaluate eligibility, thresholds or investment economics.
History preserves original inclusive parent/published intervals, supersedes and
affected windows. Gaps stay gaps; absent/closed members are not filled forward.
Same-month policy diffs are not history and one cleared reason cannot imply re-entry.

Monthly receipt/proposal/evaluation/policy/approval and v2 definition hashes exclude
root `content_hash`; membership/universe/v1 definition hashes exclude it recursively.
Independent whole-response digests bind nested locator hashes as well. Hash
self-consistency is not institutional authority. Transport DTOs never import Manage
rule validators. Existing source revision and composite custody mechanisms identify
lotus-manage truthfully, without a fabricated Performance calculation ID.

The selected monthly cut binds evaluation and observations. The retained input
universe can have a distinct cut; published receipt, membership, universe and
publication bind that input-universe cut. Original product and POLICY_INPUT locator
cuts remain unchanged and are bound by complete source and response hashes.

Configure `LOTUS_MANAGE_BASE_URL`, `LOTUS_MANAGE_READ_ACTOR_ID` and
`LOTUS_MANAGE_READ_SERVICE_IDENTITY`. Actor and service identity default to empty;
either missing value refuses before transport. They are deployment-owned, while
tenant comes only from admitted caller scope. Every bounded Manage read sends one
`X-Service-Identity`, `X-Actor-Id`, `X-Tenant-Id` and `X-Correlation-Id`, with fixed
`X-Role: REPORT_COMPOSITE_READER` and `X-Capabilities: manage.read`. The grant is not
caller-configurable and has no `manage.write` fallback. Only diagnostic propagation
headers pass through; caller Authorization, service identity, grant and role never do.
Blank, padded, comma-separated or control-character identity/correlation values
refuse before transport. Configured headers do not establish enterprise IAM.

Manage #795 owns enrollment and authorization of these existing read operations.
For example, Report settings `LOTUS_MANAGE_READ_SERVICE_IDENTITY=report-reader` and
`LOTUS_MANAGE_READ_ACTOR_ID=report-actor` with admitted tenant `tenant-A` must match
Manage's explicit `DPM_COMPOSITE_READ_SERVICE_GRANTS_JSON` enrollment:

```json
[{"service_identity":"report-reader","actor_id":"report-actor","tenant_id":"tenant-A"}]
```

This example is a controlled trusted-header enrollment, not a credential or bank
grant. Manage has no enrollment default. Published capture uses the exact receipt
resolver POST, membership/parent GETs, universe-attestation GET and publication GET.
Evaluated-only capture uses the existing exact monthly-evaluation proposal GET.
The owner must authorize those read purposes and reject mutation attempts by the
same reader, including spoofed write roles/capabilities. Policy tests, source replay
and intercepted transport do not prove live composition: enforced-auth TCP with
actual configured Report calls and negative controls is required separately.
`LOTUS_MANAGE_MAX_RESPONSE_BYTES` can reduce the 8 MiB ceiling, never raise it.
Responses are streamed and bounded before JSON parsing through existing retry
policy; compressed responses refuse on this bounded path. Nothing is truncated.
Preflight measures the full serialized Render request and visible/evidence/policy/
identity/PinnedData projection, including chunks and partition headers, before
Render/Archive calls. Existing 8 MiB request, 1,000 rows/sheet, 30,000 total rows,
210,000 cells, 16 MiB text, 64 sheets, 100 columns and 32,767 UTF-16 units/literal
limits remain unchanged. Logical tables retain 10,000 rows/32 columns. There is no
invented three-month cap or unlimited history. Actual compressed output size stays
under Render's 16 MiB writer guard; ZIP size cannot be known without writing.

Qualification is CONTROLLED_ELIGIBILITY_SOURCE_REPLAY / NOT_ATTESTED, with source
SYNTHETIC_UNSIGNED approval, UNVERIFIED population/completeness and UNAVAILABLE
official activation. COMPLETE coverage is not bank authority. Controlled unit
examples and Report worker execution do not establish genuine producer publication,
actual workbook or Archive acceptance. Complete RPT-04, original RPT-01–12,
Report #417 and parent Platform #923 remain open.

## Pooled analysis v5

`composite_review.v5` adds an exclusive pooled primary to the existing report
family. Its [schema](../contracts/composite_review.v5.schema.json) and examples retain
the entire source result, observation, bundle, raw source bodies, source pins,
membership, money, policy, correction predecessor, solver diagnostics and original
solver output. Canonical source dictionaries are not rewritten from parsed models.
Typed projection rejects nonfinite or inexact source money, mixed selected identity,
manifest, member, source body, fee, interval, policy and correction evidence.

The source owns all financial values. Exact decimal money and decimal-fraction
returns remain distinct from FLOAT64 root, rate bounds, residuals and convergence
controls. Source-stated AVAILABLE XIRR requires qualified convergence and unique-root
controls. Report checks the stated controls; it does not scan roots or solve XIRR.
NOT_CALCULABLE requires all three return values null and explicit reasons, even
when rejected original solver diagnostics contain a Dietz value. FALLBACK_ANALYSIS
requires the explicitly selected ALLOW_MODIFIED_DIETZ policy and source fallback
identity/reason; its actual method remains visible.

The complete deterministic table projection contains summary, outcome, monetary
observation, dated investor and portfolio cash flows, valuations, member controls,
source pins, membership, policy and disclosures, plus exact JSON
evidence for every source and predecessor leaf, including additive supplier fields.
Changing a cell, unit/conversion, policy, row, column, table, pointer or qualification
refuses the dataset. Return values convert decimal ratios to percent once for
display; source values remain unchanged. SourceEvidence JSON preserves booleans,
nulls and FLOAT64 diagnostics without pretending they are exact decimal money.
The immutable snapshot/revision and existing composite Archive envelope bind these
values; custody does not promote source qualification or institutional attestation.
The complete serialized dataset has an 8 MiB admission budget. XLSX preflight
checks worksheet dimensions, cell literal length and exact display projection;
oversized evidence refuses delivery without truncating the retained source.

The checked-in original/correction pair comes from qualified Performance main
`98a4befee87905fe202b72f62ceb5169a081dab7`, controlled synthetic PostgreSQL production.
Report unit proof uses recorded transport, registered ASGI and SQLite. Live
Performance principal admission, PostgreSQL and receiver XLSX/custody remain
separate acceptance gates. Genuine monthly eligibility amendment and full #417 are open.
