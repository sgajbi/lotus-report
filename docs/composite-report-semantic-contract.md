# Composite Review Semantic Contract

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

Current source dependency: Performance PR #630 at
`82fb0ac182eadc18ade2cbeff5c45ae72ca21940`. The producer limit is 120 exact
windows, with larger selections refused rather than truncated. Its explicit
selection is calculated replay, not official/freeze/control authority.

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
new candidate/version; it cannot rewrite the old snapshot. Protected producer
main, compatible Render main and real workbook/Archive reconciliation remain
required before Excel catalogue activation.

The current generic regenerate/replay command routes remain portfolio-review
only. Composite source correction requires a fresh composite order with explicit
corrected pins and a new retry identity; no command silently substitutes the
latest source. Workbook rerender preparation retains the existing archived-job
predicate and original template, but real composite Archive/rerender proof is
still required before support is advertised.
