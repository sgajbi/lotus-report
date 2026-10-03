"""Admit and present source-owned history without calculating calendar coverage."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from app.models.performance_history import (
    MAX_HISTORY_PERIODS,
    WORKSPACE_HISTORY_PERIODS,
    PerformanceHistoryCoverage,
    PerformanceHistoryQualification,
    SourceTwrSummary,
)

SOURCE_STATES = {"ready", "stale", "degraded", "empty", "error", "unsupported"}
SOURCE_REASONS = {
    "calculation_complete",
    "empty_resolved_periods",
    "insufficient_valuation_points",
    "stale_source_observations",
    "benchmark_unavailable",
    "calculation_quality_issue",
    "partial_history_coverage",
    "unknown_history_coverage",
    "unsupported_input_mode",
}
SOURCE_FRESHNESS = {"current", "same_day", "stale", "unknown"}
SOURCE_INPUT_MODES = {"stateful", "stateless"}
SOURCE_PERIODS = set(WORKSPACE_HISTORY_PERIODS)
TWR_BASES: dict[str, Literal["NET_TWR", "GROSS_TWR"]] = {"net": "NET_TWR", "gross": "GROSS_TWR"}
HISTORY_REASON_TEXT = {
    "covered_window_matches_requested_window": "required history is complete",
    "no_observations_in_requested_window": "no observations in the requested window",
    "leading_history_missing": "history is missing at the beginning of the window",
    "interior_history_missing": "history is missing within the window",
    "trailing_history_missing": "history is missing at the end of the window",
    "venue_calendar_not_attested": "venue calendar coverage is not attested",
    "explicit_ignored_dates_applied": "explicit date exclusions were applied",
    "beginning_market_value_baseline_applied": "a beginning market value baseline was applied",
}


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _period_bases(periods: dict[str, object]) -> dict[str, list[Literal["NET_TWR", "GROSS_TWR"]]]:
    return {
        period: [
            label
            for basis, label in TWR_BASES.items()
            if _valid_twr_summary(_mapping(_mapping(row).get("portfolio_twr")).get(basis))
        ]
        for period, row in periods.items()
    }


def _valid_twr_summary(block: object) -> bool:
    try:
        SourceTwrSummary.model_validate(_mapping(block).get("summary"))
    except ValidationError:
        return False
    return True


def _source_returns_valid(periods: dict[str, object]) -> bool:
    return all(
        _valid_twr_summary(block)
        for row in periods.values()
        for basis, block in _mapping(_mapping(row).get("portfolio_twr")).items()
        if basis in TWR_BASES and block is not None
    )


def _bounded_source_value(value: object, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _returned_periods(value: object) -> tuple[dict[str, object], bool]:
    periods = _mapping(value)
    if len(periods) > MAX_HISTORY_PERIODS:
        return {}, False
    admitted = {
        key: row for key, row in periods.items() if _bounded_source_value(key, SOURCE_PERIODS)
    }
    return admitted, len(admitted) == len(periods)


def _requested_periods(request: dict[str, object]) -> tuple[list[str], bool]:
    rows = request.get("periods")
    if not isinstance(rows, list) or len(rows) > MAX_HISTORY_PERIODS:
        return [], False
    admitted = [
        period
        for row in rows
        if (period := _bounded_source_value(_mapping(row).get("period"), SOURCE_PERIODS))
    ]
    return admitted, len(admitted) == len(rows)


def _scope_matches(
    result: PerformanceHistoryQualification,
    *,
    portfolio_id: str | None,
    report_end: date,
) -> bool:
    coverage = result.coverage
    return bool(
        portfolio_id
        and result.source_portfolio_id == portfolio_id
        and coverage
        and coverage.requested_end_date == report_end
        and result.source_input_mode == "stateful"
        and result.return_basis
        and all("NET_TWR" in labels for labels in result.period_return_bases.values())
        and result.requested_periods
        and set(result.returned_periods) <= set(result.requested_periods)
        and coverage.calendar_basis == result.requested_calendar_basis
    )


def _history_allows_publication(result: PerformanceHistoryQualification) -> bool:
    return (
        result.status == "complete"
        and result.source_supportability_state == "ready"
        and result.source_supportability_reason == "calculation_complete"
        and result.source_freshness_bucket in {"current", "same_day"}
        and set(result.returned_periods) == set(result.requested_periods)
    )


def qualify_performance_history(
    payload: dict[str, object],
    *,
    portfolio_id: str | None,
    as_of_date: str | None,
    source_request: dict[str, object] | None = None,
) -> dict[str, object]:
    support = _mapping(payload.get("calculation_supportability"))
    periods, periods_valid = _returned_periods(payload.get("results_by_period"))
    period_bases = _period_bases(periods)
    bases = {label for labels in period_bases.values() for label in labels}
    request = source_request or {}
    requested_periods, request_valid = _requested_periods(request)
    source_input_mode = _bounded_source_value(payload.get("input_mode"), SOURCE_INPUT_MODES)
    calendar = _mapping(request.get("calendar")).get("type", "BUSINESS")
    expected_calendar: Literal["natural_days", "business_weekdays"] = (
        "natural_days" if calendar == "NATURAL" else "business_weekdays"
    )
    result = PerformanceHistoryQualification(
        status="missing",
        source_portfolio_id=_identifier(payload.get("portfolio_id")),
        source_calculation_id=_identifier(payload.get("calculation_id")),
        source_input_mode=source_input_mode,
        source_supportability_state=_bounded_source_value(support.get("state"), SOURCE_STATES),
        source_supportability_reason=_bounded_source_value(support.get("reason"), SOURCE_REASONS),
        source_freshness_bucket=_bounded_source_value(
            support.get("freshness_bucket"), SOURCE_FRESHNESS
        ),
        returned_periods=list(periods),
        requested_periods=requested_periods,
        requested_calendar_basis=expected_calendar,
        return_basis=sorted(bases),
        period_return_bases=period_bases,
        reason_code="performance_history_qualification_missing",
    )
    if (
        not periods_valid
        or not request_valid
        or (source_input_mode is None and payload.get("input_mode") is not None)
    ):
        result.status = "invalid"
        result.reason_code = "performance_history_qualification_invalid"
        return result.model_dump(mode="json")
    raw = support.get("history_coverage")
    if raw is None:
        return result.model_dump(mode="json")
    try:
        coverage = PerformanceHistoryCoverage.model_validate(raw)
        UUID(result.source_calculation_id or "")
        report_end = date.fromisoformat(as_of_date or "")
    except (ValidationError, ValueError):
        result.status = "invalid"
        result.reason_code = "performance_history_qualification_invalid"
        return result.model_dump(mode="json")
    result.coverage = coverage
    if not _scope_matches(
        result, portfolio_id=portfolio_id, report_end=report_end
    ) or not _source_returns_valid(periods):
        result.status = "mismatched"
        result.reason_code = "performance_history_scope_mismatch"
        return result.model_dump(mode="json")
    result.status = coverage.status
    result.reason_code = {
        "complete": None,
        "partial": "partial_history_coverage",
        "unknown": "unknown_history_coverage",
    }[coverage.status]
    result.client_publication_allowed = _history_allows_publication(result)
    if coverage.status == "complete" and set(result.returned_periods) != set(
        result.requested_periods
    ):
        result.reason_code = "performance_requested_periods_incomplete"
    return result.model_dump(mode="json")


def performance_history_notes(qualification: dict[str, object]) -> list[dict[str, object]]:
    if qualification.get("client_publication_allowed") is True:
        return []
    code = qualification.get("reason_code") or "performance_source_supportability_qualified"
    return [
        {
            "code": code,
            "severity": "warning",
            "message": performance_history_statement(qualification),
        }
    ]


def performance_history_statement(qualification: dict[str, object]) -> str:
    """Visible source scope; union evidence is never recast as per-period coverage."""
    coverage = _mapping(qualification.get("coverage"))
    status = qualification.get("status", "missing")
    statement = f"Performance history qualification: {status}. "
    if coverage:
        statement += (
            f"Calculation union window requested {coverage['requested_start_date']} to "
            f"{coverage['requested_end_date']}; covered observations "
            f"{coverage['covered_start_date']} to {coverage['covered_end_date']}; "
            f"effective window {coverage['effective_start_date']} to "
            f"{coverage['effective_end_date']}. Calendar: "
            f"{str(coverage['calendar_basis']).replace('_', ' ')}; "
            f"calculation basis: {str(coverage['calculation_basis']).replace('_', ' ')}. "
            "Missing required "
            f"observations: {coverage['missing_required_observation_count']}; "
            "sample: "
            + (", ".join(_strings(coverage["missing_required_observation_dates_sample"])) or "none")
            + ". Source reasons: "
            + "; ".join(HISTORY_REASON_TEXT[code] for code in _strings(coverage["reason_codes"]))
            + ". "
        )
    statement += "Union-window evidence does not independently attest each returned period."
    if qualification.get("client_publication_allowed") is not True:
        statement += (
            " Available return figures are retained; requested history or source supportability "
            "requires advisor review before client presentation."
        )
    return statement
