# Performance history qualification

Lotus Performance owns valuation-history coverage, calendar rules, exclusions, calculation
basis and every return. Lotus Report preserves those statements and decides whether its
portfolio review can present them without qualification. Report does not fill missing dates,
invent zero returns, infer venue holidays or recalculate coverage.

## Public contract and publication policy

`performance.history_qualification` is a named `PerformanceHistoryQualification` contract.
Its `coverage` is the source's requested, covered and effective date bounds, calendar and
calculation basis, missing observation count/sample, and bounded reasons. Source calculation,
portfolio and input-mode identity, source supportability/freshness, requested and returned
periods, and `period_return_bases` bind the statement to the actual workspace request.

`coverage_scope=calculation_union_window` means one assessment over the union of resolved
period windows. Performance supplies no independent per-period history attestation. The
period/basis identities refer to that union; Report does not allocate its missing count or
effective dates to individual YTD, monthly, annual or net/gross figures.

| Condition | Report behavior |
| --- | --- |
| Complete, internally consistent history; matching portfolio/date/calendar/period/basis; ready current source | History permits publication. Independent benchmark and other section qualifications still apply. |
| Partial or unknown coverage | Keep the available sourced returns; mark Performance partial, prevent client-ready promotion and explain the history limitation. |
| Absent or malformed history | Keep compatible sourced figures with missing/invalid qualification; no completeness claim. Unknown reason vocabulary is rejected rather than displayed as arbitrary source prose. |
| Mismatched history scope, unresolved requested periods, degraded/stale/unrecognized source posture | Preserve the limitation and prevent unqualified publication. Complete history cannot override source degradation. |
| Different or absent source portfolio identity | Refuse the workspace figures before projection, contribution follow-on or reuse as analytics input. A foreign figure is not an available figure for this portfolio. |

Valid complete statements may use an explicit beginning market value baseline or date
exclusions. Covered observations may lie outside the requested window. Report validates
consistency without requiring effective bounds to equal the requested calendar endpoints.
Genuine zero and negative returns retain their signs and values. An available benchmark
does not make incomplete portfolio history ready.

Supplied net/gross cumulative and annualized TWR summaries must contain finite numeric
readings. Boolean, string and non-finite readings cannot supply displayed summary figures
or permit publication; valid readings in the same response remain available. A malformed
gross basis cannot silently disappear from admission while contributing a fee-drag figure.

## Capture and document behavior

Successful Performance workspace calls receive semantic history classification in recorded
lineage. Unknown or missing qualification cannot become complete through a text-substring
classifier. Existing transport failure, redaction and unsupported postures remain applicable.
The snapshot persists the qualification, section readiness, audience, disclosures and trust
metadata together. Unavailable independent sources retain their own stronger posture.

The additive Render block `performance_history_qualification` carries the retained evidence.
Visible `review_observations` state qualification, union scope, date bounds, basis, missing
count/sample and reviewed source reasons, including when other observations already exist.
Qualified history prevents ready/complete/quality-passed governance promotion. Render owns
the layout; the unchanged portfolio-review v1 template displays these observations on its
scope page before the performance page.

Rerender consumes the retained snapshot and does not reassess history from today's sources.
Regenerate captures fresh evidence under a new snapshot; the original payload/hash remains
unchanged. Older captures with no qualification are presented conservatively. Present but
malformed retained evidence is invalid; absence is missing. Presentation guards never rewrite
historical snapshot bytes or hashes.

## Validation boundary

Report controls cover complete/partial/unknown/missing/malformed and mismatched statements,
leading/interior/trailing gaps, return-basis and request binding, baseline/exclusion cases,
zero/negative returns, real owner HTTP adapters, native SQLite/PostgreSQL capture and fresh
interpreter reread, plus actual unchanged Render/Typst PDF output. Controlled source facts
certify Report's projection and publication policy, not upstream financial calculations.
Independent API lifecycle acceptance and protected final-head/main evidence are recorded
with each delivery rather than inferred from a successful compiler response.

This qualification is separate from analytics watchlist/reconciliation policy (#111),
benchmark policy (#241), attribution (#254), aggregation (#391), Risk qualification (#398)
and worker correlation context (#397). It does not certify IAM, production custody, capacity,
failover or platform demo readiness.
