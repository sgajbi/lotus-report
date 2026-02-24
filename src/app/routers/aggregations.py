from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.models.contracts import PortfolioAggregationResponse
from app.services.aggregation_service import AggregationService

router = APIRouter(prefix="/aggregations", tags=["Aggregations"])


@router.get(
    "/portfolios/{portfolio_id}",
    response_model=PortfolioAggregationResponse,
    summary="Get portfolio aggregation",
    description=(
        "Returns reporting-ready aggregated rows for a portfolio by as-of date. "
        "Current slice uses deterministic placeholder rows while PAS/PA connectors are integrated."
    ),
)
def get_portfolio_aggregation(
    portfolio_id: Annotated[str, Path(description="Canonical portfolio identifier.")],
    as_of_date: Annotated[str, Query(alias="asOfDate", description="Business as-of date (YYYY-MM-DD).")],
) -> PortfolioAggregationResponse:
    return AggregationService().get_portfolio_aggregation(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
    )
