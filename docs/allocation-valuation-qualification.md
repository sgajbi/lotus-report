# Allocation valuation qualification

Report preserves the valuation evidence owned by Core. In summary and review responses,
`allocation.by*` bucket `weight` is a decimal ratio and `market_value` is a reporting-currency
amount. Each is independently nullable: a known value of 120 can coexist with an unavailable
weight when another source position makes the full-scope denominator unknown. Null never
means measured zero. Known zero and signed figures remain visible without renormalization.

`allocation.valuation_coverage` preserves Core's bounded state, reason and four nonnegative
counts: snapshot rows, expected open positions, valued positions and unvalued positions.
The six states are `COMPLETE`, `MEASURED_ZERO`, `CARRY_FORWARD`, `LOADED_EMPTY`, `PARTIAL`
and `UNAVAILABLE`. Report checks state/reason/count consistency; it does not reconstruct
valuation coverage or compute look-through. Source full-scope and view totals, look-through
metadata, contributor evidence and calculation lineage remain available alongside qualification.
Every returned covered view must contain buckets; source-loaded empty views must contain none.
Supplied bucket and contributor counts must be nonnegative integers, excluding booleans.
Displayed plus omitted contributor counts must agree with a supplied contributor total; expanded
look-through bucket counts are not equated with original source snapshot counts. Contradictions
prevent complete publication on both new-source and retained presentation paths.

The named `AllocationQualification` contract states policy `allocation-valuation-v1`, source
portfolio/date/currency, admitted coverage and `client_publication_allowed`. A requested/source
portfolio, resolved date or reporting-currency conflict refuses source figures with HTTP502.
An unresolved binding on the qualified source contract also refuses projection. Missing legacy
coverage and malformed or contradictory evidence cannot become complete readiness. Invalid
coverage reasons are withheld rather than copied as arbitrary client-facing narrative.

Covered current figures can be complete. Carry-forward remains explicitly qualified. Partial
and unavailable coverage, missing numeric weights, invalid values and missing qualification
require advisor review. Client sections, disclosures, capture-call lineage, trust metadata and
the Render governance summary carry that limitation. An unknown competitor prevents a claim
of the largest bucket; its identity remains visible in the breakdown. Numeric ordering places
unavailable amounts after known amounts without using replacement zero in the ranking.

Render packages convert source ratio weights to percentage text exactly once: 1.2 and -0.2
become `120.00%` and `-20.00%`. A genuine zero becomes `0.00%` / `0.00`; unknown is
`Not available`. `allocation_valuation_qualification` and `allocation_source_evidence` carry
the source posture and lineage. Existing allocation presentation `ready`, `empty` and
`unavailable` describe dimension retrieval/selection, independently of numeric completeness.
Render owns deterministic table/chart presentation; the consumer's unknown-value support is
governed by Render #331.
No-row `UNAVAILABLE` valuation evidence is unavailable data, not an empty portfolio.
Recorded dimension selection remains fixed; presentation posture honors that distinction.

Retained captures are immutable. A snapshot without valid, matching allocation qualification
may contain a previously normalized zero whose source availability cannot be recovered.
Its presentation copy therefore suppresses allocation values and weights, retains identities
and counts, and marks completeness/readiness partial. Rerender neither rewrites the snapshot
or its historical hash nor fetches current source data. Regenerate is the separate recapture
workflow; it cannot retroactively certify a retained capture.

A genuinely absent allocation section remains neutral when the captured order explicitly
omitted it. Requested absence and present null, empty or malformed allocation containers carry
bounded qualification and a visible limitation. Retained contributor scope, scalar values,
counts and bucket identities undergo the same admission checks as new source evidence.
Invalid evidence suppresses all allocation weights so removing a malformed competitor cannot
invent a largest-bucket claim. Malformed recorded dimension decisions are refused with the
typed `allocation_presentation_invalid` validation error; valid selection and order stay fixed.

The existing public JSON numeric contract still uses finite serialization floats. This boundary
validates with Decimal first and rejects bool, malformed, nonfinite, overflow and nonzero
underflow instead of manufacturing zero. A single reviewed monetary inventory entry documents
this compatibility boundary; migration to a Decimal-safe public and retained wire contract is
separate work. No authoritative financial arithmetic is introduced by this qualification policy.

Tests use controlled Core HTTP facts plus native Report HTTP, durable SQLite/PostgreSQL capture
and a separate process reread. Actual compiled consumer evidence must name the immutable
Report/Render source revisions. This methodology does not certify production IAM, upstream
financial engines, capacity, custody or disaster recovery.
