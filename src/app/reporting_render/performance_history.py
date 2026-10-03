"""History posture for immutable snapshots, including older unattested captures."""

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.models.performance_history import PerformanceHistoryQualification, SourceTwrReturn


def snapshot_history_qualification(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    performance = snapshot.get("performance")
    if not isinstance(performance, dict) or not performance:
        return None
    raw = performance.get("history_qualification")
    try:
        qualification = PerformanceHistoryQualification.model_validate(raw)
    except ValidationError:
        qualification = PerformanceHistoryQualification(
            status="missing" if raw is None else "invalid",
            reason_code="performance_history_qualification_missing"
            if raw is None
            else "performance_history_qualification_invalid",
        )
    qualification.client_publication_allowed = (
        qualification.status == "complete"
        and qualification.coverage is not None
        and qualification.coverage.status == "complete"
        and qualification.source_supportability_state == "ready"
        and qualification.source_supportability_reason == "calculation_complete"
        and qualification.source_freshness_bucket in {"current", "same_day"}
        and qualification.client_publication_allowed
        and _snapshot_binding_matches(snapshot, performance, qualification)
    )
    return qualification.model_dump(mode="json")


def _snapshot_binding_matches(
    snapshot: dict[str, Any],
    performance: dict[str, Any],
    qualification: PerformanceHistoryQualification,
) -> bool:
    try:
        UUID(qualification.source_calculation_id or "")
    except ValueError:
        return False
    summary = performance.get("summary")
    return bool(
        qualification.coverage is not None
        and qualification.source_portfolio_id == snapshot.get("portfolio_id")
        and qualification.source_portfolio_id
        and qualification.source_input_mode == "stateful"
        and qualification.coverage.requested_end_date.isoformat() == snapshot.get("as_of_date")
        and qualification.requested_calendar_basis == qualification.coverage.calendar_basis
        and qualification.requested_periods
        and isinstance(summary, dict)
        and summary
        and set(qualification.returned_periods) == set(summary)
        and set(qualification.returned_periods) == set(qualification.requested_periods)
        and set(qualification.period_return_bases) == set(summary)
        and all("NET_TWR" in labels for labels in qualification.period_return_bases.values())
        and "NET_TWR" in qualification.return_basis
        and _presented_returns_match(summary, qualification)
    )


def _presented_returns_match(
    summary: dict[str, Any],
    qualification: PerformanceHistoryQualification,
) -> bool:
    basis_keys = {"NET_TWR": "net_cumulative_return", "GROSS_TWR": "gross_cumulative_return"}
    try:
        for period, bases in qualification.period_return_bases.items():
            row = summary[period]
            if not isinstance(row, dict):
                return False
            for basis in bases:
                SourceTwrReturn.model_validate({"base": row.get(basis_keys[basis])})
            for label, key in basis_keys.items():
                if row.get(key) is not None and label not in bases:
                    return False
                annualized = row.get(key.replace("cumulative", "annualized"))
                if annualized is not None:
                    if label not in bases:
                        return False
                    SourceTwrReturn.model_validate({"base": annualized})
    except ValidationError:
        return False
    return set(qualification.return_basis) == {
        basis for bases in qualification.period_return_bases.values() for basis in bases
    }


def qualify_governance_summary(
    summary: dict[str, Any], qualification: dict[str, Any] | None
) -> dict[str, Any]:
    if qualification is None or qualification["client_publication_allowed"]:
        return summary
    # Presentation only: never rewrite an immutable snapshot or its historical hash.
    if summary["completeness_status"] == "complete":
        summary["completeness_status"] = "partial"
    if summary["data_quality_status"] == "quality_passed":
        summary["data_quality_status"] = "quality_warning"
    if summary["readiness_status"] == "ready":
        summary["readiness_status"] = "partial"
    return summary
