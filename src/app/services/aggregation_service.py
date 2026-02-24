from datetime import UTC, datetime

from app.models.contracts import AggregationRow, AggregationScope, PortfolioAggregationResponse


class AggregationService:
    def get_portfolio_aggregation(
        self,
        portfolio_id: str,
        as_of_date: str,
    ) -> PortfolioAggregationResponse:
        scope = AggregationScope(portfolioId=portfolio_id, asOfDate=as_of_date)
        # Placeholder deterministic rows until PAS+PA connectors are added.
        rows = [
            AggregationRow(bucket="TOTAL", metric="market_value_base", value=1_250_000.0),
            AggregationRow(bucket="EQUITY", metric="weight_pct", value=45.2),
            AggregationRow(bucket="FIXED_INCOME", metric="weight_pct", value=39.8),
            AggregationRow(bucket="CASH", metric="weight_pct", value=15.0),
        ]
        return PortfolioAggregationResponse(
            scope=scope,
            generatedAt=datetime.now(UTC),
            rows=rows,
        )
