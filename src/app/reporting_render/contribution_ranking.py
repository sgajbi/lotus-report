"""Which positions explained the period, and by how much (issue #209).

Report captures a full ranked contribution set from lotus-performance and, until
this module, sent Render exactly one field of it: the single largest positive
contributor. A ranking that shows only winners is not a weaker explanation of a
period - it is a misleading one, because it reads as the explanation while
omitting half the cause.

Report owns the reporting judgement (which contributors explain the period, in
what order, and how the presented set reconciles to the whole); Render owns how
that looks. Nothing here carries geometry, styling or ordering-by-pixel.

Two honesty mechanisms are structural rather than optional:

- **Reconciliation.** A top-N of a larger set explains only part of the period.
  `presented_contribution_pct` against `explained_contribution_pct` and
  `available_count` lets the page say "these 10 of 42 explain 6.10pp of
  7.93pp" instead of implying the list is the whole story.
- **The residual.** lotus-performance computes
  ``residual = total_portfolio_return - sum_of_contributions`` and may allocate
  it back into the rows. `unexplained_residual_pct` is therefore computed here
  rather than left for a reader to subtract, and the residual-allocation posture
  travels with it: "this ranking sums to the portfolio return" and "this ranking
  falls short by 0.4pp" are different claims, and only the flag distinguishes
  them.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

POSTURE_READY = "ready"
POSTURE_EMPTY = "empty"
POSTURE_UNAVAILABLE = "unavailable"

#: How many contributors a portfolio review presents. A reporting judgement -
#: enough to explain a period without the ranking becoming a holdings list -
#: and Report's to make, because `presented_contribution_pct` describes exactly
#: this set: truncating downstream would leave a true-looking number describing
#: a set that is no longer on the page.
PRESENTED_CONTRIBUTOR_LIMIT = 10


def build_contribution_ranking(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The resolved ranking, or a posture explaining why there is none."""

    performance = _as_dict(_as_dict(snapshot.get("keyFigures")).get("performance"))
    contribution = _as_dict(_as_dict(snapshot.get("performance")).get("contribution"))
    if not contribution:
        contribution = _as_dict(performance.get("contribution"))

    if contribution.get("status") != "present":
        # The source did not compute it: a fact about the DATA, said not drawn.
        return {"posture": POSTURE_UNAVAILABLE}

    supplied_rows = [row for row in contribution.get("top_position_contributors") or [] if row]
    rows = _ranked_contributors(contribution, snapshot)
    methodology = _methodology(contribution)
    period = _text(contribution.get("period"))
    if not rows:
        if supplied_rows:
            # The source returned contributors but none carries a usable value.
            # That is NOT an empty period: no economic activity and unusable
            # evidence are different facts, and calling this `empty` would tell
            # a reader the portfolio did nothing when what actually happened is
            # that the evidence could not be read.
            return {
                "posture": POSTURE_UNAVAILABLE,
                "period": period,
                "methodology": methodology,
                "unusable_row_count": len(supplied_rows),
            }
        # Computed, with nothing to rank: a fact about the PORTFOLIO, drawn as
        # an empty statement. Indistinguishable from `unavailable` if Render had
        # to infer it from an empty list - which is why posture is authoritative.
        return {"posture": POSTURE_EMPTY, "period": period, "methodology": methodology}

    presented = _presented_with_both_signs(rows)
    total_return = _decimal(contribution.get("total_portfolio_return_pct"))
    explained = _decimal(contribution.get("total_contribution_pct"))
    presented_total = sum(
        (row["_value"] for row in presented),
        start=Decimal("0"),
    )
    return {
        "posture": POSTURE_READY,
        "period": period,
        "methodology": methodology,
        "total_portfolio_return_pct": _text_decimal(total_return),
        "explained_contribution_pct": _text_decimal(explained),
        "unexplained_residual_pct": _text_decimal(
            None if total_return is None or explained is None else total_return - explained
        ),
        "presented_contribution_pct": _text_decimal(presented_total),
        "presented_count": len(presented),
        "available_count": len(rows),
        # Rows the source supplied that carry no usable contribution value.
        # Without this the reconciliation reads as "10 of 42 of everything the
        # source knows", when part of what it returned could not be read at
        # all - a subset of a complete set and a subset of a partly-unreadable
        # set are different claims.
        "unusable_row_count": len(supplied_rows) - len(rows),
        "contributors": [_published(row) for row in presented],
    }


def _presented_with_both_signs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The largest effects, but never a one-sided page when the period was not.

    Ranking by magnitude alone can truncate to a single sign: eleven gains
    larger than the only loss produce a winners-only list, which is exactly the
    misleading output this block exists to remove - the reader concludes nothing
    detracted. So when both signs exist in the available set and the truncated
    set holds only one, the weakest presented row yields its place to the
    largest contributor of the missing sign.

    One seat, not a split budget: reserving a fixed share for each sign would
    misrepresent a period that genuinely was one-sided. This changes nothing in
    the common case where the largest effects already include both.
    """

    presented = rows[:PRESENTED_CONTRIBUTOR_LIMIT]
    if len(rows) <= PRESENTED_CONTRIBUTOR_LIMIT:
        return presented

    def sign_of(row: dict[str, Any]) -> int:
        return 1 if row["_value"] > 0 else -1 if row["_value"] < 0 else 0

    presented_signs = {sign_of(row) for row in presented}
    if len(presented_signs) > 1:
        return presented
    missing = next(
        (row for row in rows[PRESENTED_CONTRIBUTOR_LIMIT:] if sign_of(row) not in presented_signs),
        None,
    )
    if missing is None:
        # The period really is one-sided; the list is honestly single-signed.
        return presented
    return presented[:-1] + [missing]


def _ranked_contributors(
    contribution: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Both signs, ordered by the size of the effect, named for a reader.

    Contribution rows carry only identifiers; the readable name lives in
    holdings. Report performs that join so Render never joins anything.
    """

    names = _security_names(snapshot)
    rows: list[dict[str, Any]] = []
    for entry in contribution.get("top_position_contributors") or []:
        if not isinstance(entry, dict):
            continue
        value = _decimal(entry.get("total_contribution_pct"))
        if value is None:
            # A contributor with no computed contribution cannot be ranked and
            # must not be drawn as a zero - "no data" and "no movement" are
            # different statements.
            continue
        security_id = _text(entry.get("security_id"))
        rows.append(
            {
                "_value": value,
                "name": names.get(security_id) or security_id or "Not available",
                "contribution_pct": _text_decimal(value),
                "average_weight_pct": _text_decimal(_decimal(entry.get("average_weight_pct"))),
                "return_pct": _text_decimal(_decimal(entry.get("total_return_pct"))),
            }
        )
    rows.sort(key=lambda row: (-abs(row["_value"]), row["name"]))
    return rows


def _security_names(snapshot: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    holdings = _as_dict(snapshot.get("holdings"))
    for bucket in _as_dict(holdings.get("holdingsByAssetClass")).values():
        if not isinstance(bucket, list):
            continue
        for row in bucket:
            if not isinstance(row, dict):
                continue
            security_id = _text(row.get("security_id"))
            name = _text(row.get("security_name")) or _text(row.get("instrument_name"))
            if security_id and name:
                names[security_id] = name
    return names


def _methodology(contribution: dict[str, Any]) -> dict[str, Any]:
    """Required output: NET versus GROSS changes what every number means, and
    unlike a scalar there is no inferring it from the value. An absent field is
    published as absent rather than defaulted - never guessed as NET."""

    source = _as_dict(contribution.get("methodology"))
    return {
        "basis": _text(source.get("basis")) or None,
        "weighting_scheme": _text(source.get("weighting_scheme")) or None,
        "residual_allocation_applied": (
            source.get("residual_allocation_applied")
            if isinstance(source.get("residual_allocation_applied"), bool)
            else None
        ),
        "residual_allocation_basis": _text(source.get("residual_allocation_basis")) or None,
    }


def _published(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _text_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}"
