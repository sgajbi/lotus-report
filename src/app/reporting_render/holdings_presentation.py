"""What the holdings panel shows, and what it leaves out (issue #245).

The overview page draws five holdings under the heading "Portfolio scope".
For a concentrated portfolio those five nearly account for the whole; for a
diversified one they may cover a third of it, and the reader was not told
which. The only signal was that the weights do not sum to 100%, which asks a
reader to add them - the contrast-only signal this repo has rejected
everywhere else.

This is the reconciliation contribution ranking already publishes, on a more
prominent surface: a subset must never imply completeness.

The posture is here for the same reason it is in allocation and contribution.
A portfolio that genuinely holds nothing and a portfolio whose holdings could
not be sourced both produce an empty list, and those are opposite facts - one
is about the PORTFOLIO and is drawn, the other about the DATA and is said.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

POSTURE_READY = "ready"
POSTURE_EMPTY = "empty"
POSTURE_UNAVAILABLE = "unavailable"


def build_holdings_presentation(
    snapshot: dict[str, Any],
    *,
    ranked: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    """The presented holdings reconciled against the whole portfolio.

    `ranked` is the same ordered list the panel is drawn from, passed in rather
    than recomputed: a reconciliation that described a different set than the
    one on the page would be worse than none, because it would look checked.
    """

    holdings = _as_dict(snapshot.get("holdings"))
    if not holdings:
        # The HOLDINGS section was not ordered; nothing is promised.
        return {}

    grouped = holdings.get("holdingsByAssetClass")
    if not isinstance(grouped, dict):
        # The source did not answer. A fact about the DATA, said not drawn.
        return {
            "posture": POSTURE_UNAVAILABLE,
            "supportability_status": _supportability_status(holdings),
            "notes": _notes(holdings),
        }

    if not ranked:
        # Answered, with nothing held: a fact about the PORTFOLIO.
        return {
            "posture": POSTURE_EMPTY,
            "supportability_status": _supportability_status(holdings),
            "presented_count": 0,
            "available_count": 0,
            "notes": _notes(holdings),
        }

    presented = ranked[:limit]
    presented_weight = _summed_weight(presented)
    return {
        "posture": POSTURE_READY,
        "supportability_status": _supportability_status(holdings),
        "presented_count": len(presented),
        "available_count": len(ranked),
        # Absent rather than zero when no presented holding states a weight:
        # "these five cover 0% of the portfolio" is a false statement, while
        # "the covered share could not be established" is a true one. The count
        # reconciliation still holds, so the two fail independently.
        "presented_weight_pct": (None if presented_weight is None else f"{presented_weight:.2f}"),
        "notes": _notes(holdings),
    }


def _summed_weight(presented: list[dict[str, Any]]) -> Decimal | None:
    weights = [row.get("_weight") for row in presented]
    stated = [weight for weight in weights if isinstance(weight, Decimal)]
    if not stated:
        return None
    return sum(stated, start=Decimal("0"))


def _supportability_status(holdings: dict[str, Any]) -> str | None:
    """Core's own verdict on the evidence, forwarded verbatim.

    Stated rather than left to be inferred from whether `notes` is non-empty.
    Report does not recompute Core's data quality and must not: the source
    owns whether a position set is reconciled, and re-deriving it here would
    be a second opinion with no evidence behind it.

    This is why the three cases stay distinct. A portfolio that holds nothing,
    a portfolio whose holdings could not be sourced, and a portfolio whose
    holdings arrived unreconciled are three different statements about a
    client's own positions, and the first two are not degradations of trust
    while the third is.
    """

    supportability = _as_dict(holdings.get("supportability"))
    return _text(supportability.get("status")) or None


def _notes(holdings: dict[str, Any]) -> list[dict[str, Any]]:
    supportability = _as_dict(holdings.get("supportability"))
    return [
        {
            "code": _text(note.get("code")) or None,
            "severity": _text(note.get("severity")) or None,
            "message": _text(note.get("message")) or None,
        }
        for note in supportability.get("notes") or []
        if isinstance(note, dict)
    ]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""
