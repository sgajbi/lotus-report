from datetime import UTC, datetime
from typing import Any

from app.clients.pa_client import PaClient
from app.clients.pas_client import PasClient
from app.config import settings
from app.models.contracts import AggregationRow, AggregationScope, PortfolioAggregationResponse
from app.precision_policy import quantize_money, quantize_performance, quantize_quantity, to_decimal


class AggregationService:
    def __init__(self, pas_client: PasClient | None = None, pa_client: PaClient | None = None):
        self._pas_client = pas_client or PasClient(
            base_url=settings.pas_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        )
        self._pa_client = pa_client or PaClient(
            base_url=settings.pa_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        )

    async def _fetch_inputs(
        self, portfolio_id: str, as_of_date: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        summary_status, summary_payload = await self._pas_client.get_portfolio_summary(
            portfolio_id=portfolio_id,
            payload={"as_of_date": as_of_date},
        )
        if summary_status >= 400:
            summary_payload = {}

        allocation_status, allocation_payload = await self._pas_client.get_asset_allocation(
            portfolio_id=portfolio_id,
            payload={"as_of_date": as_of_date, "dimensions": ["asset_class"]},
        )
        if allocation_status >= 400:
            allocation_payload = {}

        pa_status, pa_payload = await self._pa_client.get_pas_input_twr(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            periods=["YTD"],
        )
        if pa_status >= 400:
            pa_payload = {}
        return {"summary": summary_payload, "allocation": allocation_payload}, pa_payload

    def _parse_market_value(self, position: dict[str, Any]) -> float | None:
        valuation = position.get("valuation")
        if isinstance(valuation, dict):
            for key in ("market_value_base", "market_value", "current_value_base", "current_value"):
                value = valuation.get(key)
                if value is None:
                    continue
                try:
                    return float(quantize_money(value))
                except (TypeError, ValueError):
                    continue
        for key in ("market_value_base", "market_value", "current_value_base", "current_value"):
            value = position.get(key)
            if value is None:
                continue
            try:
                return float(quantize_money(value))
            except (TypeError, ValueError):
                continue
        return None

    def _build_asset_class_rows(
        self, pas_payload: dict[str, Any], total_mv: float
    ) -> list[AggregationRow]:
        allocation = pas_payload.get("allocation", {})
        if not isinstance(allocation, dict):
            return []
        views = allocation.get("views", [])
        if not isinstance(views, list):
            return []
        asset_class_view = None
        for view in views:
            if not isinstance(view, dict):
                continue
            if str(view.get("dimension", "")).lower() == "asset_class":
                asset_class_view = view
                break
        if not isinstance(asset_class_view, dict):
            return []
        buckets = asset_class_view.get("buckets", [])
        if not isinstance(buckets, list):
            return []

        rows: list[AggregationRow] = []
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            asset_class = bucket.get("dimension_value")
            if not isinstance(asset_class, str) or not asset_class.strip():
                continue
            try:
                asset_market_value = float(
                    quantize_money(bucket.get("market_value_reporting_currency"))
                )
            except (TypeError, ValueError):
                continue
            if asset_market_value <= 0 or total_mv <= 0:
                continue
            weight = bucket.get("weight")
            if weight is None:
                weight_pct = float(
                    quantize_performance(
                        (to_decimal(asset_market_value) / to_decimal(total_mv)) * 100
                    )
                )
            else:
                try:
                    weight_pct = float(quantize_performance(to_decimal(weight) * 100))
                except (TypeError, ValueError):
                    weight_pct = float(
                        quantize_performance(
                            (to_decimal(asset_market_value) / to_decimal(total_mv)) * 100
                        )
                    )
            rows.append(
                AggregationRow(
                    bucket=str(asset_class).upper(),
                    metric="weight_pct",
                    value=weight_pct,
                )
            )

        rows.sort(key=lambda row: row.bucket)
        return rows

    def get_portfolio_aggregation(
        self,
        portfolio_id: str,
        as_of_date: str,
    ) -> PortfolioAggregationResponse:
        scope = AggregationScope(portfolioId=portfolio_id, asOfDate=as_of_date)
        # Placeholder deterministic rows until lotus-core+lotus-performance connectors are added.
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

    async def get_portfolio_aggregation_live(
        self,
        portfolio_id: str,
        as_of_date: str,
    ) -> PortfolioAggregationResponse:
        scope = AggregationScope(portfolioId=portfolio_id, asOfDate=as_of_date)
        pas_payload, pa_payload = await self._fetch_inputs(portfolio_id, as_of_date)

        summary_payload = pas_payload.get("summary", {})
        if not isinstance(summary_payload, dict):
            summary_payload = {}
        totals = summary_payload.get("totals", {})
        if not isinstance(totals, dict):
            totals = {}
        snapshot_metadata = summary_payload.get("snapshot_metadata", {})
        if not isinstance(snapshot_metadata, dict):
            snapshot_metadata = {}

        total_mv = totals.get("total_market_value_reporting_currency")
        if total_mv is None:
            total_mv = 1_250_000.0

        ytd_return = (
            pa_payload.get("resultsByPeriod", {}).get("YTD", {}).get("net_cumulative_return")
        )
        if ytd_return is None:
            ytd_return = 0.0

        try:
            position_count = int(snapshot_metadata.get("position_count", 0))
        except (TypeError, ValueError):
            position_count = 0

        rows = [
            AggregationRow(
                bucket="TOTAL",
                metric="market_value_base",
                value=float(quantize_money(total_mv)),
            ),
            AggregationRow(
                bucket="TOTAL",
                metric="position_count",
                value=float(quantize_quantity(position_count)),
            ),
            AggregationRow(
                bucket="TOTAL",
                metric="return_ytd_pct",
                value=float(quantize_performance(ytd_return)),
            ),
        ]
        rows.extend(
            self._build_asset_class_rows(
                pas_payload=pas_payload,
                total_mv=float(quantize_money(total_mv)),
            )
        )
        return PortfolioAggregationResponse(
            scope=scope,
            generatedAt=datetime.now(UTC),
            rows=rows,
        )
