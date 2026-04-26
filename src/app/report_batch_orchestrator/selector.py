from __future__ import annotations

from collections.abc import Sequence

from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    MaterializedPortfolio,
    PortfolioBatchCandidate,
)
from app.reporting_jobs.models import ReportCallerContext


class BatchSelectorValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


SUPPORTED_MATERIALIZATION_SELECTORS = {
    "explicit_portfolio_list",
    "selected_subset",
}


def materialize_portfolios(
    *,
    request: BatchCreateRequest,
    caller_context: ReportCallerContext,
) -> list[MaterializedPortfolio]:
    if request.selector_mode not in SUPPORTED_MATERIALIZATION_SELECTORS:
        raise BatchSelectorValidationError(
            "unsupported_batch_selector",
            f"Selector mode {request.selector_mode!r} is not supported for materialization yet.",
        )

    candidates = _candidate_map(request.source_candidates)
    if request.selector_mode == "explicit_portfolio_list":
        selected_ids = _explicit_portfolio_ids(request.portfolio_ids)
        selected = [_candidate_for_id(candidates, portfolio_id) for portfolio_id in selected_ids]
    else:
        selected = sorted(
            (candidate for candidate in request.source_candidates if candidate.selected),
            key=lambda candidate: candidate.portfolio_id,
        )
        if not selected:
            raise BatchSelectorValidationError(
                "empty_batch_selector",
                "Selected-subset batch requires at least one selected active portfolio.",
            )

    if len(selected) > request.max_batch_size:
        raise BatchSelectorValidationError(
            "batch_size_exceeded",
            "Batch selector materialized more portfolios than the configured maximum.",
        )

    materialized: list[MaterializedPortfolio] = []
    for candidate in selected:
        _validate_candidate(candidate, caller_context)
        materialized.append(
            MaterializedPortfolio(
                portfolio_id=candidate.portfolio_id,
                source_system=candidate.source_system,
                source_object=candidate.source_object,
            )
        )
    return materialized


def _candidate_map(
    candidates: Sequence[PortfolioBatchCandidate],
) -> dict[str, PortfolioBatchCandidate]:
    by_id: dict[str, PortfolioBatchCandidate] = {}
    duplicates: set[str] = set()
    for candidate in candidates:
        if candidate.portfolio_id in by_id:
            duplicates.add(candidate.portfolio_id)
        by_id[candidate.portfolio_id] = candidate
    if duplicates:
        raise BatchSelectorValidationError(
            "duplicate_source_portfolio",
            f"Source candidates contain duplicate portfolio ids: {', '.join(sorted(duplicates))}.",
        )
    return by_id


def _explicit_portfolio_ids(portfolio_ids: Sequence[str]) -> list[str]:
    normalized = [portfolio_id.strip() for portfolio_id in portfolio_ids if portfolio_id.strip()]
    if not normalized:
        raise BatchSelectorValidationError(
            "empty_batch_selector",
            "Explicit-list batch requires at least one portfolio id.",
        )
    duplicates = sorted(
        {portfolio_id for portfolio_id in normalized if normalized.count(portfolio_id) > 1}
    )
    if duplicates:
        raise BatchSelectorValidationError(
            "duplicate_requested_portfolio",
            f"Explicit-list batch contains duplicate portfolio ids: {', '.join(duplicates)}.",
        )
    return normalized


def _candidate_for_id(
    candidates: dict[str, PortfolioBatchCandidate],
    portfolio_id: str,
) -> PortfolioBatchCandidate:
    candidate = candidates.get(portfolio_id)
    if candidate is None:
        raise BatchSelectorValidationError(
            "portfolio_not_found",
            f"Portfolio {portfolio_id!r} was not found in the resolved source candidates.",
        )
    return candidate


def _validate_candidate(
    candidate: PortfolioBatchCandidate,
    caller_context: ReportCallerContext,
) -> None:
    if candidate.tenant_id != caller_context.tenant_id:
        raise BatchSelectorValidationError(
            "portfolio_tenant_mismatch",
            f"Portfolio {candidate.portfolio_id!r} does not belong to the caller tenant.",
        )
    if candidate.region != caller_context.region:
        raise BatchSelectorValidationError(
            "portfolio_region_mismatch",
            f"Portfolio {candidate.portfolio_id!r} does not belong to the caller region.",
        )
    if not candidate.active:
        raise BatchSelectorValidationError(
            "inactive_portfolio",
            f"Portfolio {candidate.portfolio_id!r} is inactive and cannot be reported in batch.",
        )
