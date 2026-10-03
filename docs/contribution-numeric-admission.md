# Contribution numeric admission

Report admits finite contribution evidence before mapping and retained presentation. The shared
`app.contribution_numbers` policy validates through Decimal, rejects null, booleans, malformed
values and nonfinite scalars, and preserves the exact representation of admitted source values.
Source position contributions, optional weight/return/local/FX fields, hierarchy contribution
and weight, and portfolio/explained totals follow the same policy.

A missing or invalid required contribution retains the mapped position/security identity with
a null value. It cannot enter ranking or extreme selection. Mixed usable/unusable rankings keep
`ready` retrieval posture with `available_count`, `presented_count` and `unusable_row_count`.
All-unusable evidence is `unavailable`; a genuinely empty sourced row set is `empty`.
An invalid optional field is null without discarding a usable required contribution.

Unknown source totals stay null. Report never rebuilds authoritative totals from the ranked set;
the unexplained residual is unavailable unless both source totals are finite. The presented sum
is a reporting aggregate over admitted rows. Magnitude ordering uses Decimal without float
conversion or rounding to the arithmetic context. Positive, negative and zero values remain
usable. Sign-labelled key figures select only strictly positive or strictly negative rows;
zero is still usable ranking evidence but belongs to neither sign. Zero cannot fall through
to a legacy alternative field in a retained highlight.

Retained package construction applies finite admission to ranking, contribution highlights,
deterministic observations and holding contribution, weight and return text without changing snapshot bytes or
refreshing upstream data. Current contribution fields take precedence; an explicit unknown
current field does not use a legacy fallback. A legacy highlight with only its YTD field remains
supported. Negative values cannot enter a retained positive highlight; a retained zero remains
numerically available but receives no positive-contributor name or affirmative narrative.
Contribution percentage text uses fixed decimal formatting after admission.

Regression evidence uses actual Report mapping, key-figure selection and package construction
with synthetic source/job DTOs. It establishes numeric admission and presentation semantics,
not current upstream emission, public HTTP/durable capture workflows or rendered PDF acceptance.
Successful-response shape admission, source period completeness, authoritative financial
calculation, production IAM/custody, capacity and disaster recovery are separate boundaries.
