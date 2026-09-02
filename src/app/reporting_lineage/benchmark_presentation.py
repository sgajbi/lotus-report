"""Whether this report promised a benchmark comparison (issue #241).

A performance table with no Benchmark column can mean two opposite things:
this mandate has no benchmark, or it has one and the return series could not
be sourced. Report establishes which on every capture and then dropped it at
the package boundary, so the renderer was left to infer - and inferred from
whether any row supplied a benchmark value.

That inference reads an all-unavailable comparison as "no benchmark", which
silently removes the Benchmark and Relative columns from a benchmarked
mandate's report during an upstream outage. The client receives something
indistinguishable from an unbenchmarked portfolio's report, with nothing to
ask about. On an archived document that is a durable misstatement about the
mandate, and the section-vs-job rule it breaks is explicit: a section the
order promised is never silently omitted.

The posture published here is the vocabulary the capture already uses -
``available`` / ``unavailable`` / ``not_requested`` - rather than a second set
of names for the same fact. Two names for one fact is how the availability and
capture surfaces drifted apart in #231.
"""

from __future__ import annotations

from typing import Any

POSTURE_AVAILABLE = "available"
POSTURE_UNAVAILABLE = "unavailable"
POSTURE_NOT_REQUESTED = "not_requested"

_STATED_POSTURES = frozenset({POSTURE_AVAILABLE, POSTURE_UNAVAILABLE, POSTURE_NOT_REQUESTED})

#: Recorded when a capture predates the benchmark context and the posture is
#: resolved from the order instead. The comparison cannot be proven to have
#: been sourced, so it is not claimed - but the order asked for one, so the
#: columns stay.
REASON_UNPROVEN_ON_REPLAY = "benchmark_comparison_unproven_for_capture"


def resolve_benchmark_presentation(
    *,
    options: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """The stated posture of this report's benchmark comparison.

    ``available``     the comparison was ordered and sourced - draw it;
    ``unavailable``   ordered and NOT sourced - draw the columns and say why,
                      because the reader is entitled to the comparison and
                      needs to know it is missing;
    ``not_requested`` this mandate has no benchmark - draw no columns.

    Presence of values cannot express the difference between the last two, and
    inferring it back from them is the inference this contract removes.
    """

    performance = _as_dict(snapshot.get("performance"))
    if not performance:
        # The performance section was not composed; there is no comparison to
        # describe, and claiming one either way would be an invention.
        return {}

    benchmark = _as_dict(performance.get("benchmark"))
    posture = _text(benchmark.get("comparison_status"))
    notes = _notes(performance)

    if posture in _STATED_POSTURES:
        return {
            "posture": posture,
            "benchmark_code": _text(benchmark.get("benchmark_code")) or None,
            "return_source": _text(benchmark.get("return_source")) or None,
            "reason_code": _text(benchmark.get("reason_code")) or None,
            "notes": notes,
        }

    # A capture taken before the benchmark context existed. Resolve from what
    # the ORDER asked for, never from the values in the table: reading the
    # values is exactly the inference that loses a benchmarked mandate's
    # columns, and a rerender is the case most likely to matter.
    ordered_code = _text(options.get("benchmark_code")) or None
    if ordered_code is None:
        return {
            "posture": POSTURE_NOT_REQUESTED,
            "benchmark_code": None,
            "return_source": None,
            "reason_code": None,
            "notes": notes,
        }
    return {
        "posture": POSTURE_UNAVAILABLE,
        "benchmark_code": ordered_code,
        "return_source": None,
        "reason_code": REASON_UNPROVEN_ON_REPLAY,
        "notes": notes,
    }


def _notes(performance: dict[str, Any]) -> list[dict[str, Any]]:
    supportability = _as_dict(performance.get("supportability"))
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
