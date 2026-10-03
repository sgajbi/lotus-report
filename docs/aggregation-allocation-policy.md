# Portfolio aggregation allocation policy

`GET /aggregations/portfolios/{portfolio_id}` composes source facts. Core owns portfolio valuation,
classification and source-stated allocation weights. Report owns this endpoint's presentation
aggregate. Its supported basis is `signed_net_reporting_currency`, not gross exposure.

For a positive net market value `D` and signed bucket market value `V_i`, the derived percentage is
`P_i = V_i / D * 100`. Both monetary inputs must be in the same reporting currency. Report uses
the raw source denominator rather than its displayed amount rounded to cents. Decimal computation
precedes the existing API numeric serialization; percentages use six decimals and half-even rounding.
Equity120/cash-20/net100 returns120%/-20%. Shorts and measured zero buckets remain in the view.
Report never renormalizes the retained positive subset.

A source-stated weight is preserved after percentage conversion and rounding, provided it agrees
with the signed basis to one percentage rounding unit (`0.000001` percentage points). Derivation is
allowed only when a legacy bucket omits the weight field. Explicit null, non-numeric, Boolean and
non-finite values are unavailable evidence. Report does not repair or replace them.

Before publication, bucket values must reconcile with the raw net denominator within one cent.
Repeated bucket labels, malformed buckets, incompatible source/summary totals, or conflicting stated
currencies, or unstated currency on either source, withhold all weight rows. A nonpositive net
denominator is unsupported even if the source
supplies weights. Declared PARTIAL/UNAVAILABLE or malformed allocation valuation coverage is likewise
unsupported. Unstated legacy valuation coverage is not promoted into a source certification claim.

`allocation_supportability` accompanies every response: basis, available/empty/unavailable status,
bounded reason, source/derived/mixed weight provenance when accepted, and source currency when stated.
`unavailable_sources` continues to identify calls that failed or omitted other required metrics.
Allocation qualification does not replace that transport evidence. Source total market value,
position count and independently measured return rows survive an unavailable allocation breakdown.

The requested Performance metric is the YTD NET cumulative base return from
`results_by_period.YTD.portfolio_twr.net.summary.cumulative_return.base`. HTTP 200 admits it only
when present, numeric and finite through the existing six-decimal, half-even precision policy and
public numeric serialization. Missing/null/malformed containers, booleans, unusable scalars,
nonfinite values and conversion failures omit the return row and add one bounded
`lotus-performance` qualification with `status_code=200` and `reason=incomplete_payload`.
Report never substitutes zero, another period or another basis. Measured finite zero and signed
returns remain available. Admission happens while the source HTTP status is present: 202 stays
`pending` and failed responses stay `no_response`, even if their payload resembles a completed
calculation. Core count and Performance gaps remain independently qualified; measured valuation
and supported allocation rows survive the Performance gap. Raw source diagnostics are not exposed.

Successful source evidence also carries `performance_history_qualification`, the existing named
source-owned calculation union-window contract. Report binds it to the actual YTD NET request,
portfolio, input mode, end date and calendar expectation; the unchanged request defaults to
business-weekday coverage. Requested, covered and effective dates, missing count/sample,
calculation/calendar basis and safe source reasons remain explicit. Partial/unknown/missing/invalid
history does not erase a compatible available return, but prevents unqualified client publication.
Complete history cannot override degraded/stale/unrecognized source posture or an unavailable
requested return. Numeric strings retain the existing aggregation conversion policy, while raw
string readings cannot manufacture stronger history attestation under the shared typed policy.
Absent or different source portfolio identity withholds the return with bounded incomplete evidence
and mismatched history, including when the source omits coverage. Pending/failed transport carries
null history qualification; completed-looking response bodies cannot override the source status.

History remains separate from `unavailable_sources`: that list denotes an absent metric, whereas
partial history may accompany an available measured return. `coverage_scope=calculation_union_window`
does not independently attest each period. Calculation-ID admission validates UUID syntax, not a
request-correlated calculation identity; the shared policy binds requested end date without
independently deriving YTD start or coverage. Explicit source baseline/exclusion statements and
covered observations outside the request remain valid. See
[performance history policy](performance-history-qualification.md).

The shared public metadata guard admits only recognized input modes and workspace period identities,
with a fourteen-entry period budget before projection. Discarded unsupported metadata yields fixed
invalid qualification, never stronger completeness; arbitrary source diagnostic labels are omitted.

The policy applies to this aggregation endpoint. Portfolio review/snapshot/Render null qualification
uses its separately governed valuation policy. Controlled client fixtures
and native Report HTTP prove composition and serialization, not production IAM, actual source engine
calculation, rendered documents, capacity, or client publication.
