"""Why a risk figure is missing, not merely that it is (issue #234).

The risk panel prints "Not available" for a metric it cannot show. That one
string currently stands for five different facts, and two of them point an
operator in opposite directions:

- ``missing_benchmark`` - no benchmark was ordered, so a benchmark-relative
  measure is meaningless for this mandate. Permanent and expected.
- ``risk_upstream_failure`` - lotus-risk could not answer. Transient, and
  re-running the report may well succeed.

A reader seeing "Beta: Not available" cannot tell "this portfolio has no
benchmark" from "our risk service was down when we generated this". The first
is a statement about the mandate; the second is a statement about the data.

Report already establishes which one it is. `_risk_supportability` computes a
bounded status and typed notes on every capture, and the whole thing reaches
JSON consumers - then the render package drops it, so the same ordered section
explains itself in JSON and stays silent in PDF. This module forwards what is
already known rather than inventing a second vocabulary for it.

Bounding is deliberate and asymmetric: the POSTURE is bounded because it
drives whether the page says anything at all, while note codes and severities
are forwarded verbatim because they inform a human rather than a branch. A
code Report does not recognise is still the operator's join key, and replacing
it with a guess would erase the one field that identifies the fault.
"""

from __future__ import annotations

from typing import Any

POSTURE_READY = "ready"
POSTURE_PARTIAL = "partial"
POSTURE_UNAVAILABLE = "unavailable"

#: The postures `_risk_supportability` establishes. Anything else is a fault in
#: our own capture, not a posture to publish.
_STATED_POSTURES = frozenset({POSTURE_READY, POSTURE_PARTIAL, POSTURE_UNAVAILABLE})

#: Recorded when the capture produced a risk section without saying how well it
#: is supported. Deliberately NOT defaulted to `ready`: a status we failed to
#: establish is not a clean bill of health, and publishing one would be the
#: same defect this module exists to remove.
REASON_SUPPORTABILITY_UNSTATED = "risk_supportability_unstated"


def build_risk_posture(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The stated support posture for the risk section, or nothing at all."""

    risk_analytics = _as_dict(snapshot.get("riskAnalytics"))
    if not risk_analytics:
        # The section was not ordered. Nothing was promised, so there is
        # nothing to explain - the same reason the panel itself is not drawn.
        return {}

    supportability = _as_dict(risk_analytics.get("supportability"))
    status = _text(supportability.get("status"))
    if status not in _STATED_POSTURES:
        return {
            "posture": POSTURE_UNAVAILABLE,
            "notes": [
                {
                    "code": REASON_SUPPORTABILITY_UNSTATED,
                    "severity": "blocking",
                    "message": (
                        "The risk capture did not state how well this section is "
                        "supported, so its figures cannot be presented as established."
                    ),
                }
            ],
        }

    return {
        "posture": status,
        "notes": [_published_note(note) for note in _notes(supportability)],
    }


def _notes(supportability: dict[str, Any]) -> list[dict[str, Any]]:
    return [note for note in supportability.get("notes") or [] if isinstance(note, dict)]


def _published_note(note: dict[str, Any]) -> dict[str, Any]:
    """A note as an operator needs it: the identifying code, kept verbatim.

    `period` is published only when the note is about one period, so a
    section-wide fault is never read as a per-period one.
    """

    published = {
        "code": _text(note.get("code")) or None,
        "severity": _text(note.get("severity")) or None,
        "message": _text(note.get("message")) or None,
    }
    period = _text(note.get("period"))
    if period:
        published["period"] = period
    return published


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""
