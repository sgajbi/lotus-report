"""How well the risk section is supported, and by what evidence (issue #234).

Separated from the read service because it is a judgement about evidence
rather than a read: given what lotus-risk returned, which measures can be
presented and which absences must be explained. It has one caller and no I/O.

The vocabulary here is the one the render package forwards, so an operator
sees the same bounded reason on the job record, in the JSON report, and behind
the "Not available" a reader sees on the page.
"""

from __future__ import annotations

from typing import Any

#: Metrics lotus-risk computes only when a benchmark is supplied. Notes about a
#: missing benchmark name these, so a consumer can say which measures a mandate
#: fact covers without keeping its own copy of the list.
BENCHMARK_RISK_METRICS = ("BETA", "TRACKING_ERROR", "INFORMATION_RATIO")


def risk_supportability(
    *,
    results: dict[str, Any],
    metadata: dict[str, Any],
    benchmark_code: str | None,
    period_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The section's support status and the notes that justify it.

    `benchmark_code` is passed in already resolved rather than dug out of the
    request here: whether a benchmark was ordered is the caller's fact, and
    resolving it twice is how two answers to one question start to differ.
    """

    notes: list[dict[str, Any]] = []
    if not results:
        notes.append(
            {
                "code": "missing_return_history",
                "severity": "blocking",
                "message": "lotus-risk returned no period results for the selected request.",
            }
        )

    risk_free_context = _as_dict(metadata.get("risk_free_context"))
    if risk_free_context.get("requested") and risk_free_context.get("reason") == "ZERO_RATE":
        notes.append(
            {
                "code": "missing_risk_free_rate",
                "severity": "informational",
                "message": (
                    "Risk-adjusted return uses the lotus-risk zero-rate convention because "
                    "no source-backed risk-free rate was applied."
                ),
                # Sharpe is captured and not presented, so a consumer can tell
                # this note concerns nothing on the page.
                "metrics": ["SHARPE"],
            }
        )

    for failure in period_failures or []:
        notes.append(
            {
                "code": failure.get("code") or "risk_period_upstream_failure",
                "severity": "warning",
                "period": failure.get("period"),
                "message": failure.get("message")
                or "Risk metrics are unavailable for this period.",
            }
        )

    benchmark_context = _as_dict(metadata.get("benchmark_context"))
    if benchmark_code is None:
        notes.append(
            {
                "code": "missing_benchmark",
                "severity": "informational",
                "message": (
                    "Benchmark-relative risk posture is unavailable because no benchmark "
                    "code was provided."
                ),
                "metrics": list(BENCHMARK_RISK_METRICS),
            }
        )
    elif not benchmark_context.get("requested"):
        notes.append(
            {
                "code": "missing_benchmark",
                "severity": "warning",
                "message": (
                    "Benchmark-relative risk posture is unavailable because benchmark "
                    "return series is not sourced for the risk calculation."
                ),
                "metrics": list(BENCHMARK_RISK_METRICS),
            }
        )

    severities = {note.get("severity") for note in notes}
    if "blocking" in severities:
        status_value = "unavailable"
    elif "warning" in severities:
        status_value = "partial"
    else:
        status_value = "ready"
    return {"status": status_value, "notes": notes}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
