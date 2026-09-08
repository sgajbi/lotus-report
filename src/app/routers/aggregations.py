from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Query, status

from app.models.contracts import PortfolioAggregationResponse
from app.services.aggregation_service import AggregationService

router = APIRouter(prefix="/aggregations", tags=["Aggregations"])


def _admitted_tenant(tenant_id: str | None) -> str:
    """The admitted tenant, or a refusal. Never a substitute.

    This route previously admitted no tenant at all, so every Core and
    Performance call it made was recorded against an absent owner -- at
    lotus-core, which is the service that actually owns portfolio ownership and
    the one placed to refuse a foreign portfolio.

    Refusing is the only safe answer to a missing header. Defaulting would
    manufacture an ownership claim indistinguishable from a real one, which is
    the defect #177 removed from the batch scheduler; and passing the absence
    through would leave the aggregation attributable to nobody while looking
    like it succeeded.

    Whitespace is stripped before the emptiness test, so a header of spaces is
    refused rather than admitted as a tenant no upstream can match.
    """
    admitted = (tenant_id or "").strip()
    if not admitted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_caller_context",
                "message": "Required caller context headers are missing.",
                "missing_headers": ["X-Tenant-Id"],
            },
        )
    return admitted


@router.get(
    "/portfolios/{portfolio_id}",
    response_model=PortfolioAggregationResponse,
    summary="Get portfolio aggregation",
    description=(
        "Returns reporting-ready aggregated rows for a portfolio by as-of date. "
        "Current slice uses deterministic placeholder rows while "
        "lotus-core/lotus-performance connectors are integrated."
    ),
)
async def get_portfolio_aggregation(
    portfolio_id: Annotated[str, Path(description="Canonical portfolio identifier.")],
    as_of_date: Annotated[date, Query(description="Business as-of date (YYYY-MM-DD).")],
    live: Annotated[
        bool,
        Query(
            description=(
                "If true, fetches lotus-core and "
                "lotus-performance upstream contracts "
                "before aggregation."
            ),
            examples=[True],
        ),
    ] = True,
    tenant_id: Annotated[
        str | None,
        Header(
            alias="X-Tenant-Id",
            description="Tenant identifier for entitlement and audit. Required.",
        ),
    ] = None,
) -> PortfolioAggregationResponse:
    admitted_tenant_id = _admitted_tenant(tenant_id)
    service = AggregationService()
    if live:
        return await service.get_portfolio_aggregation_live(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            admitted_tenant_id=admitted_tenant_id,
        )
    return service.get_portfolio_aggregation(portfolio_id=portfolio_id, as_of_date=as_of_date)
