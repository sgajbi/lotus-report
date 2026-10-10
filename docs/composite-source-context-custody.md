# Composite source context: inception and supplied history

The existing `POST /reports/composite-reviews` accepts optional `source_context`
beside a primary TWR `selection` and optional existing explicit `source_products`.
This selects `composite_review.v8`.
The context contains one exact Manage v1 `definition` pin (definition_version,
content_hash, response_digest), a strict `since_inception` boolean, and up to two
exact `memberships` pins (membership_revision, content_hash, response_digest).
At least SI or history must be selected. Existing calendar/trailing products can
coexist with SI and supplied history in this same immutable manifest. Linked,
pooled and monthly eligibility operations retain their primary selectors;
v1–v7 contracts are unchanged.

Report captures the exact definition and membership revision GETs through its
configured Manage read identity and the existing Performance explicit vector.
It retains raw source responses and Report response digests together. Definition
v1 canonical hashing recursively omits content_hash; membership v1 omits its own
top-level content_hash. Source hashes and raw response digests are distinct guards.
Tenant, composite, definition version/currency and every financial window's
definition hash must agree. No latest lookup, replacement definition, calculator,
policy approval or account-closure status is inferred.

## Since inception

`SinceInception` is present only when selected. Its single `since_inception` row
contains these fields:

| Field | Meaning/source |
| --- | --- |
| inception_date | Captured original Manage date |
| period_start / period_end | Exact inclusive primary Performance coverage |
| return | Source cumulative_return for complete READY inception coverage |
| availability / reason | AVAILABLE/NONE or UNAVAILABLE with a coverage/source reason |
| coverage_convention | INCLUSIVE_SOURCE_INCEPTION; no other baseline supported |
| definition_version / definition_hash / definition_response_digest | Captured definition identity |

SI reuses the primary vector; this bounded profile accepts no supplemental SI
vector. For a full report, select the primary from inception to the report date,
then select existing calendar/trailing products as exact primary subvectors.
For example: primary Jan01–Dec31 with 12 pinned monthly windows; calendar_2026
with those same 12 windows and its own exact Performance response pin;
trailing_2m with the identical Nov/Dec window pins and its own response pin.
With since_inception=true and explicit memberships, one worker package contains
MonthlyReturns, AnnualReturns, TrailingReturns, SinceInception and MembershipHistory.
No shorter primary or latest response substitutes for full inception capture.
The primary may begin mid-month. It must start on captured inception to
qualify as SI. A valid later window retains its Summary/PeriodReturns value while
SI is null with `SOURCE_VECTOR_BEGINS_AFTER_PINNED_INCEPTION`. A non-READY vector
yields `COMPLETE_READY_SOURCE_VECTOR_REQUIRED`. Missing/gapped periods or source
failures cannot create SI facts. Coverage before inception or after a supplied
termination date is refused. A source ratio `0.030200000000` displays `3.02%`;
Report does not calculate, link or annualize it. Accepted R27 Apr09–10 against
Apr08 inception remains window-only evidence.

## Supplied membership history

When selected, full pinned snapshots replace the unavailable MembershipHistory
placeholder. Their hashes cover every referenced primary financial window.
An additional snapshot is allowed only as the direct parent of a referenced
correction. Duplicate, foreign, mismatched, missing or unreferenced revisions are
refused. The bounded two-revision profile admits an original and its direct
correction, not an arbitrary predecessor chain. Monthly approval is not required
for these explicitly supplied decisions, and this evidence cannot satisfy a
monthly eligibility publication selector.

Rows `r{capture_index}:d{decision_index}` preserve original source order and every
interval, including dates outside the financial cut. Columns are membership_revision,
supersedes_membership_revision, affected_from, affected_to, portfolio_id,
effective_from, effective_to, status, reason_code, discretionary, approval_ref and
source_snapshot_id. They use TEXT policies with direct raw source pointers.
The discretionary boolean displays lowercase true/false. Open effective_to is
null/NOT_APPLICABLE with OPEN_ENDED_INTERVAL; other source nulls are
NOT_APPLICABLE/SOURCE_FIELD_NOT_APPLICABLE. C's supplied Jan15 end does not become
an account-closure assertion. Gaps are preserved; no decisions are filled forward.

## Custody and consumer boundary

V8 retains definition, source_context pins, supplied_memberships, existing explicit
source_products and the primary response in one immutable snapshot. V2 calendar/
trailing admission and projection are reused, including scope, complete-month
coverage and exact primary-subvector rules. Definition and supplied membership
joins cover every selected vector. Both Manage and Performance participate in
revision identity, including each supplied membership hash/revision and every
explicit product calculation fingerprint. Archive identity retains all product
pins and response digests. Admission
rebuilds the complete projection and rejects forged values, reasons, pointers,
units, omitted rows or conflicting pins. Technical rerender makes no source calls.
Aggregate raw capture, including all explicit product responses, is capped at
8 MiB and eight products; the existing full workbook preflight
also measures request, cells, rows, text, sheets and literals before Render I/O.

XLSX ordering requires current support for exactly `composite-review/v8`, refusing
503 while unavailable. Render's current strict v1–v7 profiles cannot implicitly
accept these attachments. Actual v8 XLSX/Archive certification awaits consumer
support and independent artifact reconciliation. Publication stays NOT_ATTESTED.

Controlled executable examples live in `contracts/examples/composite-review.v8.*.json`.
The positive SI example supplies +1% then +2% and cumulative 3.02%. The history
example uses Manage's supplied A/B/C decision shapes with controlled literal
Jan05 financial facts. These prove software contracts, not the pending actual
January source-financial join, positive native SI, or institutional population.
The cohesive original/corrected caller examples additionally demonstrate the
full-inception/calendar/trailing/history recipe with controlled literal annual
source facts. Both packages replay unchanged with both sources offline; the
unaffected trailing product stays byte-identical across the correction.

From the `lotus-report` checkout with its development Python, PowerShell:

```powershell
python -m pytest tests/unit/composite_reporting/test_inception_contract.py tests/unit/composite_reporting/test_inception_delivery.py tests/unit/composite_reporting/test_supplied_history.py
```

Bash, from the same checkout:

```bash
python -m pytest tests/unit/composite_reporting/test_inception_contract.py tests/unit/composite_reporting/test_inception_delivery.py tests/unit/composite_reporting/test_supplied_history.py
```

Registered tests use SQLite and controlled source/capability/Render transports.
The receiver deliberately declines rendering after actual package capture;
these tests do not claim a produced XLSX or completed Archive record. #417 remains
open for the source and consumer acceptance boundaries above.
