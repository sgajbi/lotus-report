"""Pre-order availability for the ADVISOR_COMMENTARY section (issue #166).

Answers, per portfolio and report context, "does an accepted Performance
Advisor Brief exist?" BEFORE an order is placed, by resolving lotus-ai's
latest-accepted lookup (lotus-ai#183). The answer is composed truth, not a
recreation: lotus-ai owns which run is latest-accepted; Report owns only the
mapping onto its section-availability vocabulary.

Mapping, deliberate:

- 200 with a verified identity echo -> ``ready`` with the accepted brief's
  run id, so the ordering flow can carry ``advisor_brief_run_id`` forward
  into the order without a second discovery step.
- ``no_accepted_run`` -> ``advisor_brief_not_reviewed`` (no accepted brief
  asserts this portfolio - the advisor has not accepted one yet).
- ``no_context_match`` -> ``advisor_brief_context_mismatch`` (accepted
  briefs exist for the portfolio, none assert the requested date/currency).
- Everything else - transport failure, 5xx, bounded 409 refusals, or a 200
  whose payload does not verify - -> ``advisor_brief_availability_unknown``.
  Claiming ``advisor_brief_not_reviewed`` when the lookup merely failed
  would assert a fact nobody proved; the section is unavailable either way,
  but the reason must stay truthful.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from app.report_ordering_catalogue.models import (
    AdvisorCommentaryAcceptedBrief,
    AdvisorCommentaryAvailabilityResponse,
)
from app.reporting_metrics import record_report_operation

ADVISOR_BRIEF_PACK_FAMILY = "advisor_brief"

REASON_ACCEPTED = "advisor_brief_accepted"
REASON_NOT_REVIEWED = "advisor_brief_not_reviewed"
REASON_CONTEXT_MISMATCH = "advisor_brief_context_mismatch"
REASON_AVAILABILITY_UNKNOWN = "advisor_brief_availability_unknown"


class LatestAcceptedBriefClient(Protocol):
    async def get_latest_accepted_brief(
        self,
        *,
        portfolio_id: str,
        tenant_id: str,
        as_of_date: str | None = None,
        reporting_currency: str | None = None,
    ) -> tuple[int, dict[str, Any]]: ...


async def resolve_advisor_commentary_availability(
    *,
    ai_client: LatestAcceptedBriefClient,
    portfolio_id: str,
    tenant_id: str,
    as_of_date: str | None = None,
    reporting_currency: str | None = None,
) -> AdvisorCommentaryAvailabilityResponse:
    started_at = perf_counter()
    try:
        status_code, payload = await ai_client.get_latest_accepted_brief(
            portfolio_id=portfolio_id,
            tenant_id=tenant_id,
            as_of_date=as_of_date,
            reporting_currency=reporting_currency,
        )
    except Exception:
        return _record(_unknown("The advisor-brief lookup could not be reached."), started_at)

    if status_code == 200:
        brief = _verified_brief(payload, portfolio_id=portfolio_id)
        if brief is None:
            # A 200 that does not verify is indistinguishable from a wrong
            # answer; never turn it into "ready".
            return _record(
                _unknown("The advisor-brief lookup answered with an unverifiable payload."),
                started_at,
            )
        return _record(
            AdvisorCommentaryAvailabilityResponse(
                state="ready",
                reason_code=REASON_ACCEPTED,
                message=(
                    "An accepted Performance Advisor Brief exists for this portfolio and "
                    "context; ordering the section will compose exactly this reviewed run."
                ),
                accepted_brief=brief,
            ),
            started_at,
        )

    if status_code == 404:
        reason = _lookup_reason(payload)
        if reason == "no_accepted_run":
            return _record(
                AdvisorCommentaryAvailabilityResponse(
                    state="unavailable",
                    reason_code=REASON_NOT_REVIEWED,
                    message=(
                        "No accepted Performance Advisor Brief exists for this portfolio; "
                        "review and accept the brief in the Workbench first."
                    ),
                ),
                started_at,
            )
        if reason == "no_context_match":
            return _record(
                AdvisorCommentaryAvailabilityResponse(
                    state="unavailable",
                    reason_code=REASON_CONTEXT_MISMATCH,
                    message=(
                        "Accepted briefs exist for this portfolio, but none assert the "
                        "requested report date or currency."
                    ),
                ),
                started_at,
            )

    return _record(
        _unknown("The advisor-brief lookup could not answer for this portfolio."), started_at
    )


def _record(
    response: AdvisorCommentaryAvailabilityResponse, started_at: float
) -> AdvisorCommentaryAvailabilityResponse:
    record_report_operation(
        operation="advisor_commentary_availability",
        status=response.state,
        failure_category=(None if response.state == "ready" else response.reason_code),
        duration_seconds=perf_counter() - started_at,
    )
    return response


def _unknown(message: str) -> AdvisorCommentaryAvailabilityResponse:
    return AdvisorCommentaryAvailabilityResponse(
        state="unavailable",
        reason_code=REASON_AVAILABILITY_UNKNOWN,
        message=(
            f"{message} The section cannot be offered until availability can be proven; "
            "this does not mean no accepted brief exists."
        ),
    )


def _lookup_reason(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        reason = metadata.get("reason_code")
        if isinstance(reason, str):
            return reason
    return None


def _verified_brief(
    payload: dict[str, Any], *, portfolio_id: str
) -> AdvisorCommentaryAcceptedBrief | None:
    run_id = _clean_str(payload.get("run_id"))
    content_hash = _clean_str(payload.get("content_hash"))
    context = payload.get("context")
    review = payload.get("review")
    if not run_id or not content_hash or not isinstance(context, dict):
        return None
    if not isinstance(review, dict):
        return None
    reviewed_by = _clean_str(review.get("reviewed_by"))
    reviewed_at = _clean_str(review.get("reviewed_at"))
    if not reviewed_by or not reviewed_at:
        return None
    # Identity echo: the lookup must answer for the portfolio that was asked.
    if _clean_str(context.get("portfolio_id")) != portfolio_id:
        return None
    return AdvisorCommentaryAcceptedBrief(
        run_id=run_id,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        content_hash=content_hash,
        as_of_date=_clean_str(context.get("as_of_date")),
        reporting_currency=_clean_str(context.get("reporting_currency")),
        period=_clean_str(context.get("period")),
    )


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
