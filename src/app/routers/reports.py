from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query

from app.models.contracts import (
    PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE,
    PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE,
    PortfolioReviewReportRequest,
    PortfolioReviewReportResponse,
)
from app.services.reporting_read_service import ReportingReadService

router = APIRouter(prefix="/reports", tags=["Reports"])


def get_reporting_read_service() -> ReportingReadService:
    return ReportingReadService()


def _apply_requested_section_limit(payload: dict[str, Any], section_limit: int) -> dict[str, Any]:
    limited_payload = dict(payload)
    sections = limited_payload.get("sections")
    if isinstance(sections, list) and len(sections) > section_limit:
        limited_payload["sections"] = sections[:section_limit]
    return limited_payload


@router.post(
    "/portfolios/{portfolio_id}/summary",
    response_model=dict[str, Any],
    summary="Get portfolio summary (lotus-report-owned)",
    description=(
        "lotus-report-owned reporting endpoint for consolidated portfolio summary. "
        "Phase-1 source is lotus-core upstream while ownership moves to lotus-report."
    ),
)
async def get_portfolio_summary(
    portfolio_id: Annotated[str, Path(description="Canonical portfolio identifier.")],
    request: dict[str, Any],
    section_limit: Annotated[int, Query(ge=1, le=20, description="pagination")] = 10,
    service: ReportingReadService = Depends(get_reporting_read_service),
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> dict[str, Any]:
    return await service.get_portfolio_summary(
        portfolio_id=portfolio_id,
        request_payload=_apply_requested_section_limit(request, section_limit),
        correlation_id=correlation_id,
    )


@router.post(
    "/portfolios/{portfolio_id}/review",
    response_model=PortfolioReviewReportResponse,
    summary="Get portfolio review report",
    description=(
        "Returns the lotus-report-owned portfolio review contract for front-office client/advisor "
        "review meetings. The response is machine-readable JSON with sourced client profile, key "
        "figures, client-ready sections, advisor-only sections, report coverage, observations, "
        "evidence lineage, deterministic advisor briefing, presentation structure, and guarded "
        "AI-readiness metadata. The endpoint composes authoritative lotus-core, lotus-performance, "
        "and lotus-risk data and marks unsupported suitability, mandate-control, "
        "target-allocation, tax-lot, realized-gain/loss, and advice features as not sourced "
        "rather than inventing report content."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE,
                    "examples": {
                        "full_portfolio_review": {
                            "summary": "Full portfolio review request",
                            "value": PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE,
                        }
                    },
                }
            }
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE,
                        "examples": {
                            "full_portfolio_review": {
                                "summary": "Full portfolio review response",
                                "value": PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        },
    },
)
async def get_portfolio_review(
    portfolio_id: Annotated[
        str,
        Path(
            description=(
                "Canonical portfolio identifier. Use PB_SG_GLOBAL_BAL_001 for governed local "
                "front-office proof unless a different dataset is explicitly required."
            )
        ),
    ],
    request: PortfolioReviewReportRequest,
    section_limit: Annotated[
        int,
        Query(
            ge=1,
            le=20,
            description=(
                "Maximum number of requested section keys to honor from the request body. This "
                "is a guardrail, not result-row pagination."
            ),
        ),
    ] = 10,
    service: ReportingReadService = Depends(get_reporting_read_service),
    correlation_id: Annotated[
        str | None,
        Header(
            alias="X-Correlation-ID",
            description="Caller-supplied correlation id propagated to upstream service calls.",
        ),
    ] = None,
) -> dict[str, Any]:
    return await service.get_portfolio_review(
        portfolio_id=portfolio_id,
        request_payload=_apply_requested_section_limit(
            request.model_dump(exclude_none=True, mode="json"), section_limit
        ),
        correlation_id=correlation_id,
    )
