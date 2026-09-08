from datetime import date

import pytest

from app.application_errors import ReportingUpstreamError
from app.services.aggregation_service import AggregationService


class _StubCoreQueryClient:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return (
            200,
            {
                "portfolio_id": portfolio_id,
                "totals": {"total_market_value_reporting_currency": 999_999.0},
                "snapshot_metadata": {"position_count": 3},
            },
        )

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return (
            200,
            {
                "scope": {"portfolio_id": portfolio_id},
                "views": [
                    {
                        "dimension": "asset_class",
                        "buckets": [
                            {
                                "dimension_value": "EQUITY",
                                "weight": 0.5,
                                "market_value_reporting_currency": 499_999.5,
                            },
                            {
                                "dimension_value": "FIXED_INCOME",
                                "weight": 0.3000003000003,
                                "market_value_reporting_currency": 300_000.0,
                            },
                            {
                                "dimension_value": "CASH",
                                "weight": 0.2000002000002,
                                "market_value_reporting_currency": 200_000.0,
                            },
                        ],
                    }
                ],
            },
        )


class _StubPerformanceClient:
    async def get_workspace_summary(
        self, payload: dict[str, object], *, admitted_tenant_id: str = ""
    ):
        return (
            200,
            {
                "results_by_period": {
                    "YTD": {
                        "portfolio_twr": {"net": {"summary": {"cumulative_return": {"base": 4.2}}}}
                    }
                }
            },
        )


class _StubCoreQueryClientWithMalformedSummary(_StubCoreQueryClient):
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 200, "not-a-dict"


@pytest.mark.asyncio
async def test_live_aggregation_uses_upstream_payloads():
    service = AggregationService(
        core_query_client=_StubCoreQueryClient(), performance_client=_StubPerformanceClient()
    )
    response = await service.get_portfolio_aggregation_live(
        portfolio_id="P1",
        as_of_date=date(2026, 2, 24),
        admitted_tenant_id="tenant-test",
    )
    metric_map = {row.metric: row.value for row in response.rows}
    assert metric_map["market_value_base"] == 999_999.0
    assert metric_map["position_count"] == 3.0
    assert metric_map["return_ytd_pct"] == 4.2

    bucket_metric_map = {(row.bucket, row.metric): row.value for row in response.rows}
    assert round(bucket_metric_map[("EQUITY", "weight_pct")], 2) == 50.0
    assert round(bucket_metric_map[("FIXED_INCOME", "weight_pct")], 2) == 30.0
    assert round(bucket_metric_map[("CASH", "weight_pct")], 2) == 20.0


@pytest.mark.asyncio
async def test_live_aggregation_refuses_a_summary_without_a_market_value():
    """A 200 carrying no total is a contract violation, not an empty portfolio.

    `total_market_value_reporting_currency` is how the summary states a
    portfolio worth nothing, so its absence is missing evidence rather than a
    zero. Substituting one invented the only number this response is really
    about.
    """
    service = AggregationService(
        core_query_client=_StubCoreQueryClientWithMalformedSummary(),
        performance_client=_StubPerformanceClient(),
    )

    with pytest.raises(ReportingUpstreamError) as refusal:
        await service.get_portfolio_aggregation_live(
            portfolio_id="P1",
            as_of_date=date(2026, 2, 24),
            admitted_tenant_id="tenant-test",
        )

    assert refusal.value.detail["code"] == "aggregation_source_incomplete"


def test_build_asset_class_rows_ignores_non_asset_class_views():
    service = AggregationService(
        core_query_client=_StubCoreQueryClient(),
        performance_client=_StubPerformanceClient(),
    )

    rows = service._build_asset_class_rows(
        core_query_payload={
            "allocation": {
                "views": [
                    {"dimension": "sector", "buckets": [{"dimension_value": "TECH"}]},
                ],
            }
        },
        total_mv=1_000_000.0,
    )

    assert rows == []


class _FailingCoreQueryClient:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 503, {"detail": "unavailable"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 503, {"detail": "unavailable"}


class _FailingPerformanceClient:
    async def get_workspace_summary(
        self, payload: dict[str, object], *, admitted_tenant_id: str = ""
    ):
        return 503, {"detail": "unavailable"}


@pytest.mark.asyncio
async def test_live_aggregation_refuses_when_the_summary_source_fails():
    """The defect this replaced: refusing sources produced a confident answer.

    This test previously asserted `market_value_base == 1_250_000.0`,
    `position_count == 0.0` and `return_ytd_pct == 0.0` against upstreams that
    had all failed -- so the fabrication was not merely tolerated, it was the
    stated requirement. Measured on the real service before the change:
    upstream 401, 403, 404 and 503 each produced exactly those three values.

    A summary that did not arrive is not a portfolio worth 1.25 million.
    """
    service = AggregationService(
        core_query_client=_FailingCoreQueryClient(), performance_client=_FailingPerformanceClient()
    )

    with pytest.raises(ReportingUpstreamError) as refusal:
        await service.get_portfolio_aggregation_live(
            portfolio_id="P1",
            as_of_date=date(2026, 2, 24),
            admitted_tenant_id="tenant-test",
        )

    detail = refusal.value.detail
    assert detail["code"] == "aggregation_source_unavailable"
    assert detail["service"] == "lotus-core"
    assert "status_code" in detail, "the refusal must name what the upstream returned"
