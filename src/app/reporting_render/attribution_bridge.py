"""Why the portfolio outperformed, as a bridge Render can draw (issue #254).

The agreed AttributionBridge shape: total -> named parts -> explicit residual
-> reconciled sum. Every figure is lotus-performance's - the effects, the
authoritative level totals, the reconciliation and the source-classified
residual are forwarded, never recomputed. Report's contribution is naming
(reader labels for group keys), posture, and the reconciliation voice shared
with contribution, holdings and the earnings statement.

Three rules carried from the contract:

- **The residual is presented, never allocated away.** Render draws it as a
  labelled bridge segment; whether it is small is the source's classification
  (``residual_materiality``), not an invitation to hide it.
- **Totals are the source's authoritative fields.** lotus-performance states
  explicitly that downstream systems must not infer totals by summing visible
  rows, and this block obeys: the level totals and the reconciliation arrive
  from their own fields.
- **A failed or pending period is said, not drawn.** ``pending`` carries the
  calculation identity so the page can state that regenerating collects the
  finished result; per-period reasons travel as notes with source-composed
  prose.

Each effect row carries ``grouping_dimension`` and ``level`` - the hierarchy
slot defined day one, one level shipped - so deeper levels ride this same
shape without a contract break.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

POSTURE_READY = "ready"
POSTURE_PENDING = "pending"
POSTURE_UNAVAILABLE = "unavailable"

_PRESENTED_PERIOD = "YTD"


def build_attribution_bridge(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The resolved bridge, or a posture explaining why there is none."""

    attribution = _as_dict(snapshot.get("attribution"))
    if not attribution:
        # The section was not ordered; nothing is promised.
        return {}

    request = _as_dict(attribution.get("request"))
    envelope: dict[str, Any] = {
        "period": _PRESENTED_PERIOD,
        "benchmark_code": _text(request.get("benchmark_code")) or None,
        "metric_basis": _text(request.get("metric_basis")) or None,
        "notes": _notes(attribution),
    }

    status = _text(attribution.get("status"))
    if status == "pending":
        accepted = _as_dict(attribution.get("accepted"))
        return {
            **envelope,
            "posture": POSTURE_PENDING,
            # The page states this rather than waiting: the calculation exists
            # upstream, and regenerating the report collects it.
            "calculation_id": _text(accepted.get("calculation_id")) or None,
        }
    if status != "present":
        return {**envelope, "posture": POSTURE_UNAVAILABLE}

    period = _as_dict(_as_dict(attribution.get("results_by_period")).get(_PRESENTED_PERIOD))
    if not period:
        # Captured as present, but the presented period is not in the results:
        # a fact about the data, said - never an empty chart.
        return {**envelope, "posture": POSTURE_UNAVAILABLE}

    level = _first_level(period)
    if level is None:
        return {**envelope, "posture": POSTURE_UNAVAILABLE}

    reconciliation = _as_dict(period.get("reconciliation"))
    materiality = _as_dict(reconciliation.get("residual_materiality"))
    dimension = _text(level.get("dimension")) or None

    return {
        **envelope,
        "posture": POSTURE_READY,
        "model": _text(attribution.get("model")) or None,
        "linking": _text(attribution.get("linking")) or None,
        "grouping_dimension": dimension,
        "level": 1,
        "period_status": _text(period.get("status")) or None,
        "effects": [_effect_row(group, dimension=dimension) for group in _groups(level)],
        # The source's authoritative totals - never a sum of the rows above.
        "totals": {
            "allocation_pp": _pp(level.get("allocation_total_pct")),
            "selection_pp": _pp(level.get("selection_total_pct")),
            "interaction_pp": _pp(level.get("interaction_total_pct")),
            "total_effect_pp": _pp(level.get("total_effect_pct")),
        },
        "reconciliation": {
            "total_active_return_pp": _pp(reconciliation.get("total_active_return")),
            "sum_of_effects_pp": _pp(reconciliation.get("sum_of_effects")),
            "residual_pp": _pp(reconciliation.get("residual")),
            # The source classifies its own residual; Report forwards the
            # verdict rather than deciding what is small.
            "residual_classification": _text(materiality.get("classification")) or None,
            "residual_treatment": _text(materiality.get("treatment")) or None,
        },
        "period_notes": _period_notes(period),
    }


def _effect_row(group: dict[str, Any], *, dimension: str | None) -> dict[str, Any]:
    key = _as_dict(group.get("key"))
    raw_label = _text(key.get(dimension)) if dimension else ""
    return {
        # The hierarchy slot, present from day one so deeper levels ride the
        # same row shape without a contract break.
        "grouping_dimension": dimension,
        "level": 1,
        "group_key": raw_label or None,
        # Reader label: the source keys are lowercase identifiers; the page
        # speaks the allocation page's vocabulary. Naming is Report's join,
        # exactly as security names are for contribution and earnings.
        "group_label": raw_label.replace("_", " ").title() if raw_label else "Not available",
        "allocation_pp": _pp(group.get("allocation")),
        "selection_pp": _pp(group.get("selection")),
        "interaction_pp": _pp(group.get("interaction")),
        "total_effect_pp": _pp(group.get("total_effect")),
        "portfolio_weight_avg_pct": _pp(group.get("portfolio_weight_avg")),
        "benchmark_weight_avg_pct": _pp(group.get("benchmark_weight_avg")),
        "portfolio_return_pp": _pp(group.get("portfolio_return")),
        "benchmark_return_pp": _pp(group.get("benchmark_return")),
    }


def _first_level(period: dict[str, Any]) -> dict[str, Any] | None:
    levels = period.get("levels")
    if not isinstance(levels, list):
        return None
    for level in levels:
        if isinstance(level, dict):
            return level
    return None


def _groups(level: dict[str, Any]) -> list[dict[str, Any]]:
    return [group for group in level.get("groups") or [] if isinstance(group, dict)]


def _period_notes(period: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-period reasons, with the source's own prose.

    Render draws only notes that carry a message (the reader-prose rule from
    the contract); a reason without one is still recorded for the operator.
    """

    return [
        {
            "code": _text(reason.get("code")) or None,
            "severity": _text(reason.get("severity")) or None,
            "message": _text(reason.get("message")) or None,
        }
        for reason in period.get("reasons") or []
        if isinstance(reason, dict)
    ]


def _notes(attribution: dict[str, Any]) -> list[dict[str, Any]]:
    supportability = _as_dict(attribution.get("supportability"))
    return [
        {
            "code": _text(note.get("code")) or None,
            "severity": _text(note.get("severity")) or None,
            "message": _text(note.get("message")) or None,
        }
        for note in supportability.get("notes") or []
        if isinstance(note, dict)
    ]


def _pp(value: Any) -> str | None:
    decimal_value = _decimal(value)
    if decimal_value is None:
        return None
    return f"{decimal_value.quantize(Decimal('0.01'))}"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""
