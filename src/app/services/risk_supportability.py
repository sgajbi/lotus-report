"""How well the risk section is supported, and by what evidence (issue #234).

Separated from the read service because it is a judgement about evidence
rather than a read: given what lotus-risk returned, which measures can be
presented and which absences must be explained. It has one caller and no I/O.

The vocabulary here is the one the render package forwards, so an operator
sees the same bounded reason on the job record, in the JSON report, and behind
the "Not available" a reader sees on the page.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.reporting_lineage.models import SnapshotPosture, UpstreamFailureCategory


class _RiskSupportability(BaseModel):
    model_config = ConfigDict(strict=True)
    state: Literal[
        "ready", "stale", "degraded", "empty", "error", "permission_blocked", "unsupported"
    ]
    reason: Literal[
        "calculation_complete",
        "benchmark_unavailable",
        "group_return_series_unavailable",
        "calculation_quality_issue",
        "insufficient_aligned_observations",
        "insufficient_observations",
        "no_return_observations",
        "permission_blocked",
        "stale_source_observations",
        "unsupported_input_mode",
    ]
    freshness_bucket: Literal["current", "same_day", "stale", "unknown"]


class _ReturnsEvidence(BaseModel):
    """Consumed Performance v1 wire; mirror its discrete quantization, never repair it."""

    model_config = ConfigDict(strict=True)
    source_service: Literal["lotus-performance"]
    calculation_id: str
    contract_version: Literal["v1"]
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    calculation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    freshness: Literal["current", "stale"]
    requested_points: int = Field(ge=0)
    returned_points: int = Field(ge=0)
    missing_points: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_identity_and_coverage(self) -> _ReturnsEvidence:
        UUID(self.calculation_id)
        if self.requested_points != self.returned_points + self.missing_points:
            raise ValueError("coverage counts do not reconcile")
        expected = self.returned_points / self.requested_points if self.requested_points else 1.0
        if abs(self.coverage_ratio - expected) > 1e-12 and self.coverage_ratio != round(
            expected, 8
        ):
            raise ValueError("coverage ratio does not reconcile")
        return self


class _RiskScope(BaseModel):
    model_config = ConfigDict(strict=True)
    as_of_date: str
    reporting_currency: str | None
    net_or_gross: Literal["NET", "GROSS"]


def qualify_risk_response(response: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Admit current calculate/rolling authority once for reader and capture.

    Risk does not echo portfolio identity. Retain the request and opaque source
    identities, but bind only the scope its public wire actually supplies.
    """
    metadata = _as_dict(response.get("metadata"))
    if not metadata.get("calculation_supportability") or not metadata.get(
        "source_returns_evidence"
    ):
        return {"state": "unknown", "reason": "risk_source_qualification_missing"}
    try:
        support = _RiskSupportability.model_validate(metadata["calculation_supportability"])
        evidence = _ReturnsEvidence.model_validate(metadata["source_returns_evidence"])
        scope = _RiskScope.model_validate(response.get("scope"))
        date.fromisoformat(scope.as_of_date)
    except (ValidationError, ValueError, TypeError):
        return {"state": "unknown", "reason": "risk_source_qualification_invalid"}
    requested = _as_dict(request.get("stateful_input"))
    if (
        metadata.get("contract_version") != "v1"
        or response.get("source_service", "lotus-risk") != "lotus-risk"
        or response.get("input_mode", "stateful") != "stateful"
        or scope.as_of_date != requested.get("as_of_date")
        or scope.reporting_currency != requested.get("reporting_currency")
        or scope.net_or_gross != requested.get("net_or_gross", "NET")
    ):
        return {"state": "unknown", "reason": "risk_source_identity_mismatch"}
    if support.state == "ready" and (
        evidence.freshness != "current"
        or evidence.missing_points
        or support.freshness_bucket not in {"current", "same_day"}
        or support.reason != "calculation_complete"
    ):
        return {"state": "unknown", "reason": "risk_source_qualification_inconsistent"}
    return {
        "state": support.state,
        "reason": support.reason,
        "freshness_bucket": support.freshness_bucket,
        "scope": dict(response["scope"]),
        "source_returns_evidence": dict(metadata["source_returns_evidence"]),
    }


def risk_call_posture(
    qualification: dict[str, Any],
) -> tuple[SnapshotPosture, SnapshotPosture, UpstreamFailureCategory, str | None]:
    state = qualification["state"]
    if state == "ready":
        return "complete", "complete", "none", None
    if state == "unsupported":
        return "not_supported", "not_supported", "unsupported_input", qualification["reason"]
    if state == "error":
        return "error", "error", "upstream_error", qualification["reason"]
    if state in {"empty", "permission_blocked"}:
        return "unavailable", "unavailable", "partial_data", qualification["reason"]
    return "partial", "partial", "partial_data", qualification["reason"]


def risk_source_supportability(qualification: dict[str, Any]) -> dict[str, Any]:
    """Project producer states into existing Report presentation postures."""
    state = qualification["state"]
    status = (
        "ready"
        if state == "ready"
        else (
            "unavailable"
            if state in {"empty", "error", "permission_blocked", "unsupported"}
            else "partial"
        )
    )
    notes = (
        []
        if state == "ready"
        else [
            {
                "code": qualification["reason"],
                "severity": "blocking" if status == "unavailable" else "warning",
                "message": (
                    f"lotus-risk source qualification is {state}: {qualification['reason']}."
                ),
            }
        ]
    )
    return {"status": status, "notes": notes, "source_qualification": qualification}


def combine_risk_qualifications(periods: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep every admitted fallback response; a later ready period cannot erase a fault."""
    if not periods:
        return {"state": "unknown", "reason": "risk_source_qualification_missing"}
    precedence = {
        state: index
        for index, state in enumerate(
            [
                "ready",
                "degraded",
                "stale",
                "unknown",
                "empty",
                "unsupported",
                "permission_blocked",
                "error",
            ]
        )
    }
    worst = max(periods, key=lambda period: precedence[period["qualification"]["state"]])
    return {**worst["qualification"], "periods": periods}


def combine_risk_section_supportability(
    calculation: dict[str, Any], trend: dict[str, Any]
) -> dict[str, Any]:
    """The ordered section includes both point-in-time and rolling evidence."""
    if not trend.get("notes"):
        return calculation
    notes = list(calculation.get("notes", []))
    notes.extend(note for note in trend["notes"] if note not in notes)
    status = calculation["status"]
    if status == "ready":
        status = "partial"
    return {
        **calculation,
        "status": status,
        "notes": notes,
        "rolling_source_qualification": trend.get("source_qualification"),
    }


def risk_trend_response(
    response: dict[str, Any], request: dict[str, Any], status_code: int
) -> dict[str, Any]:
    """Project rolling transport and typed evidence without deriving a financial value."""
    options = _as_dict(_as_dict(request.get("stateful_input")).get("rolling_options"))
    available = 200 <= status_code < 300
    support = (
        risk_source_supportability(qualify_risk_response(response, request))
        if available
        else {
            "status": "unavailable",
            "notes": [
                {
                    "code": "risk_trend_upstream_failure",
                    "severity": "blocking",
                    "message": (
                        "Risk trend is unavailable because lotus-risk could not "
                        "calculate rolling metrics."
                    ),
                }
            ],
        }
    )
    return {
        "source": {"service": "lotus-risk", "endpoint": "/analytics/risk/rolling-metrics"},
        "request": {
            "window_observations": options["window_lengths"][0],
            "metrics": options["metrics"],
            "frequency": "daily",
        },
        "supportability": support,
        "results": _as_dict(response.get("results")) if available else {},
        "metadata": _as_dict(response.get("metadata")) if available else {},
    }


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
    source_qualification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The section's support status and the notes that justify it.

    `benchmark_code` is passed in already resolved rather than dug out of the
    request here: whether a benchmark was ordered is the caller's fact, and
    resolving it twice is how two answers to one question start to differ.
    """

    source = risk_source_supportability(
        source_qualification or {"state": "unknown", "reason": "risk_source_qualification_missing"}
    )
    notes: list[dict[str, Any]] = []
    if not results:
        notes.append(
            {
                "code": "missing_return_history",
                "severity": "blocking",
                "message": "lotus-risk returned no period results for the selected request.",
            }
        )

    notes.extend(source["notes"])
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
    return {
        "status": status_value,
        "notes": notes,
        "source_qualification": source["source_qualification"],
    }


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
