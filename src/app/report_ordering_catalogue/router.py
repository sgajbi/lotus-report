from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.clients.ai_client import AiClient
from app.config import settings
from app.observability import correlation_id_var, trace_id_var
from app.report_ordering_catalogue.advisor_commentary_availability import (
    LatestAcceptedBriefClient,
    resolve_advisor_commentary_availability,
)
from app.report_ordering_catalogue.models import (
    REPORT_ORDERING_CATALOGUE_EXAMPLE,
    AdvisorCommentaryAvailabilityResponse,
    ReportOrderingCatalogueResponse,
)
from app.report_ordering_catalogue.service import (
    ReportOrderingCatalogueService,
    build_report_ordering_catalogue_service,
)

router = APIRouter(prefix="/integration", tags=["Integration"])


def get_report_ordering_catalogue_service() -> ReportOrderingCatalogueService:
    return build_report_ordering_catalogue_service()


@router.get(
    "/report-ordering-catalogue",
    response_model=ReportOrderingCatalogueResponse,
    summary="Get report ordering catalogue",
    description=(
        "Returns the versioned, Report-owned business catalogue used by product consumers to "
        "present supported report families, ordering modes, formats, configuration fields, and "
        "sections. Availability reflects live rendering supportability. Caller entitlement and "
        "portfolio eligibility remain Gateway responsibilities."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_ORDERING_CATALOGUE_EXAMPLE,
                        "examples": {
                            "available_catalogue": {
                                "summary": "Source-backed report choices",
                                "value": REPORT_ORDERING_CATALOGUE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        }
    },
)
async def get_report_ordering_catalogue(
    service: Annotated[
        ReportOrderingCatalogueService,
        Depends(get_report_ordering_catalogue_service),
    ],
) -> ReportOrderingCatalogueResponse:
    return await service.get_catalogue(
        correlation_id=correlation_id_var.get() or None,
        trace_id=trace_id_var.get() or None,
    )


def get_advisor_brief_lookup_client() -> LatestAcceptedBriefClient:
    return AiClient(
        base_url=settings.ai_base_url,
        timeout_seconds=settings.upstream_timeout_seconds,
        max_retries=settings.upstream_max_retries,
        retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
    )


@router.get(
    "/report-ordering-catalogue/advisor-commentary-availability",
    response_model=AdvisorCommentaryAvailabilityResponse,
    summary="Get pre-order availability of the advisor commentary section",
    description=(
        "Answers, for one portfolio and report context, whether the ADVISOR_COMMENTARY "
        "section can be ordered: ready (with the accepted brief run id the order must "
        "carry) exactly when lotus-ai holds an accepted, non-superseded Performance "
        "Advisor Brief whose asserted context matches; unavailable otherwise with a "
        "bounded reason - advisor_brief_not_reviewed (none accepted for the portfolio), "
        "advisor_brief_context_mismatch (accepted briefs exist, none assert the requested "
        "date or currency), or advisor_brief_availability_unknown (the lookup could not "
        "answer; deliberately distinct from not_reviewed because a failed lookup proves "
        "nothing). Report never regenerates or edits narrative; consumers compose this "
        "into portfolio-scoped ordering options."
    ),
)
async def get_advisor_commentary_availability(
    portfolio_id: Annotated[str, Query(min_length=1)],
    tenant_id: Annotated[str, Header(alias="X-Tenant-Id", min_length=1)],
    ai_client: Annotated[
        LatestAcceptedBriefClient,
        Depends(get_advisor_brief_lookup_client),
    ],
    as_of_date: Annotated[str | None, Query()] = None,
    reporting_currency: Annotated[str | None, Query()] = None,
) -> AdvisorCommentaryAvailabilityResponse:
    return await resolve_advisor_commentary_availability(
        ai_client=ai_client,
        portfolio_id=portfolio_id.strip(),
        tenant_id=tenant_id.strip(),
        as_of_date=as_of_date.strip() if as_of_date and as_of_date.strip() else None,
        reporting_currency=(
            reporting_currency.strip()
            if reporting_currency and reporting_currency.strip()
            else None
        ),
    )
