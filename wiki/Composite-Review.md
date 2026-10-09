# Composite Review

Report #417 introduces an internal composite dataset foundation on the existing
report lifecycle. Full RPT-01–12 acceptance remains open. The catalogue currently
admits JSON only; prepared Excel composition does not imply supported delivery.
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
disclosures are source-backed. Annual returns, risk, complete member universe,
eligibility reasons/history, attribution and restatement remain explicit uncaptured
products. Qualification is `EXPLICIT_RETAINED_CALCULATED_REPLAY`, publication state
`NOT_ATTESTED`; the authority receipt/control revision are unavailable.

## Workbook custody and acceptance

The prepared composer reads the persisted snapshot record and checks its revision
binding. Archive metadata has `portfolio_scope=composite`, null `portfolio_id`,
the real `composite_id` and `composite_report_identity` carrying exact selection
and three lifecycle digests. It preserves revision and document references.

SQLite and isolated PostgreSQL API/worker/process-retention proofs include an
actual registered Performance response from merged main
`c100c885752c86b8d950d7970c99a8d223e6376a`. The captured producer uses controlled
economic/provider/authority inputs; Report replays the exact captured transport.
Regression tests preserve available `0E-12` dispersion and external source
identity, and refuse fee mismatch or changed financial content. A candidate XLSX
family still requires test-only admission and stops at a controlled Render boundary.
Supplier exact-main release and fresh workbook, original/corrected/rerender custody
qualification remain required before public Excel support. Manage #714,
Performance #540/#610, Gateway #820 and institutional authority remain owner dependencies.
