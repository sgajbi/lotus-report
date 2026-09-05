from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status

from app.application_errors import (
    ReportingApplicationError,
    ReportingNotFoundError,
    ReportingUpstreamError,
    ReportingValidationError,
)
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


def _reporting_application_error_to_http(exc: ReportingApplicationError) -> HTTPException:
    if isinstance(exc, ReportingValidationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.detail)
    if isinstance(exc, ReportingNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail)
    if isinstance(exc, ReportingUpstreamError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.detail)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=exc.detail)


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
        "Returns the lotus-report-owned summary payload for one portfolio and business date. "
        "Use this endpoint when a consumer needs a consolidated report-oriented summary instead "
        "of lower-level aggregation rows."
    ),
)
async def get_portfolio_summary(
    portfolio_id: Annotated[str, Path(description="Canonical portfolio identifier.")],
    request: dict[str, Any],
    section_limit: Annotated[
        int,
        Query(
            ge=1,
            le=20,
            description="Maximum number of requested section keys honored from the request body.",
        ),
    ] = 10,
    service: ReportingReadService = Depends(get_reporting_read_service),
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> dict[str, Any]:
    try:
        return await service.get_portfolio_summary(
            portfolio_id=portfolio_id,
            request_payload=_apply_requested_section_limit(request, section_limit),
            correlation_id=correlation_id,
        )
    except ReportingApplicationError as exc:
        raise _reporting_application_error_to_http(exc) from exc


@router.post(
    "/portfolios/{portfolio_id}/review",
    response_model=PortfolioReviewReportResponse,
    summary="Get portfolio review report",
    description=(
        "Returns the lotus-report-owned portfolio review contract for front-office client and "
        "advisor review meetings. Use this endpoint when a consumer needs machine-readable JSON "
        "with client-ready and advisor-only reporting content assembled from authoritative "
        "upstream Lotus domains. Unsupported enterprise-grade content remains explicitly marked as "
        "not sourced rather than inferred."
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
    tenant_id: Annotated[
        str | None,
        Header(
            alias="X-Tenant-Id",
            description=(
                "Admitted caller tenant. When present it is recorded as the "
                "evidence pack's caller-admitted tenant; when absent the "
                "evidence carries no tenant claim (tenant_admission="
                "unattributed_caller). Source-verified tenancy is a separate, "
                "stronger posture."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    try:
        return await service.get_portfolio_review(
            portfolio_id=portfolio_id,
            request_payload=_apply_requested_section_limit(
                request.model_dump(exclude_none=True, mode="json"), section_limit
            ),
            correlation_id=correlation_id,
            # Normalized at the boundary: whitespace-padded or blank header
            # values must not become admitted evidence that fails exact
            # matching against the canonical tenant.
            admitted_tenant_id=(tenant_id.strip() or None) if tenant_id else None,
        )
    except ReportingApplicationError as exc:
        raise _reporting_application_error_to_http(exc) from exc
