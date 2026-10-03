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

The policy applies to this aggregation endpoint. Portfolio review/snapshot/Render null qualification
is tracked by #399; aggregation Performance evidence is tracked by #391. Controlled client fixtures
and native Report HTTP prove composition and serialization, not production IAM, actual source engine
calculation, rendered documents, capacity, or client publication.
