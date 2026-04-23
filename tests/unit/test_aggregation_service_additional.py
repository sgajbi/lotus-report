import pytest

from app.services.aggregation_service import AggregationService


class _CoreQueryOkClient:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "totals": {"total_market_value_reporting_currency": 1000.0},
            "snapshot_metadata": {"position_count": 0},
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"views": []}


class _PerformanceOkClient:
    async def get_workspace_summary(self, payload: dict[str, object]):
        return (
            200,
            {
                "results_by_period": {
                    "YTD": {
                        "portfolio_twr": {"net": {"summary": {"cumulative_return": {"base": 1.0}}}}
                    }
                }
            },
        )


class _CoreQueryFailClient:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 503, {"detail": "down"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 503, {"detail": "down"}


class _PerformanceFailClient:
    async def get_workspace_summary(self, payload: dict[str, object]):
        return 503, {"detail": "down"}


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ({"valuation": {"market_value_base": 10}}, 10.0),
        ({"valuation": {"market_value": 11}}, 11.0),
        ({"valuation": {"current_value_base": 12}}, 12.0),
        ({"valuation": {"current_value": 13}}, 13.0),
        ({"market_value_base": 14}, 14.0),
        ({"market_value": 15}, 15.0),
        ({"current_value_base": 16}, 16.0),
        ({"current_value": 17}, 17.0),
        ({"valuation": {"market_value_base": "bad"}, "market_value": 18}, 18.0),
        ({}, None),
    ],
)
def test_parse_market_value_variants(position, expected):
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    assert service._parse_market_value(position) == expected


def test_build_asset_class_rows_sorts_and_ignores_non_positive_values():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    payload = {
        "allocation": {
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {
                            "dimension_value": "BOND",
                            "weight": 0.25,
                            "market_value_reporting_currency": 25,
                        },
                        {
                            "dimension_value": "EQUITY",
                            "weight": 0.75,
                            "market_value_reporting_currency": 75,
                        },
                        {
                            "dimension_value": "CASH",
                            "weight": -0.05,
                            "market_value_reporting_currency": -5,
                        },
                    ],
                }
            ]
        }
    }
    rows = service._build_asset_class_rows(core_query_payload=payload, total_mv=100.0)
    assert [row.bucket for row in rows] == ["BOND", "EQUITY"]
    row_map = {row.bucket: row.value for row in rows}
    assert row_map["BOND"] == 25.0
    assert row_map["EQUITY"] == 75.0


@pytest.mark.parametrize(
    ("payload", "total_mv"),
    [
        ({}, 100.0),
        ({"allocation": []}, 100.0),
        ({"allocation": {"views": []}}, 100.0),
        ({"allocation": {"views": [{"dimension": "asset_class", "buckets": "bad"}]}}, 100.0),
        ({"allocation": {"views": [{"dimension": "asset_class", "buckets": []}]}}, 0.0),
    ],
)
def test_build_asset_class_rows_handles_non_conforming_payloads(payload, total_mv):
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    assert service._build_asset_class_rows(core_query_payload=payload, total_mv=total_mv) == []


@pytest.mark.asyncio
async def test_fetch_inputs_drops_upstream_payloads_when_services_fail():
    service = AggregationService(
        core_query_client=_CoreQueryFailClient(), performance_client=_PerformanceFailClient()
    )
    core_query_payload, performance_payload = await service._fetch_inputs("P1", "2026-02-24")
    assert core_query_payload == {"summary": {}, "allocation": {}}
    assert performance_payload == {}


def test_get_portfolio_aggregation_non_live_returns_deterministic_rows():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    response = service.get_portfolio_aggregation("P1", "2026-02-24")
    assert response.scope.portfolio_id == "P1"
    assert str(response.scope.as_of_date) == "2026-02-24"
    assert len(response.rows) == 4
    assert response.rows[0].bucket == "TOTAL"


def test_parse_market_value_returns_none_when_non_numeric_position_key():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    assert service._parse_market_value({"market_value_base": "n/a"}) is None


def test_build_asset_class_rows_returns_empty_when_total_market_value_non_positive():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    payload = {
        "allocation": {
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {
                            "dimension_value": "EQUITY",
                            "weight": 0.3,
                            "market_value_reporting_currency": 30,
                        }
                    ],
                }
            ]
        }
    }
    assert service._build_asset_class_rows(core_query_payload=payload, total_mv=-1.0) == []


def test_build_asset_class_rows_ignores_non_dict_positions():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    payload = {
        "allocation": {
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        "bad",
                        {"dimension_value": "EQUITY", "market_value_reporting_currency": 20},
                    ],
                }
            ]
        }
    }
    rows = service._build_asset_class_rows(core_query_payload=payload, total_mv=100.0)
    assert len(rows) == 1
    assert rows[0].bucket == "EQUITY"
    assert rows[0].value == 20.0


def test_build_asset_class_rows_handles_invalid_bucket_fields_and_weight_fallback():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    payload = {
        "allocation": {
            "views": [
                "bad-view",
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {"dimension_value": " ", "market_value_reporting_currency": 20},
                        {
                            "dimension_value": "BROKEN",
                            "market_value_reporting_currency": "not-money",
                        },
                        {
                            "dimension_value": "EQUITY",
                            "weight": "not-a-weight",
                            "market_value_reporting_currency": 40,
                        },
                    ],
                },
            ]
        }
    }

    rows = service._build_asset_class_rows(core_query_payload=payload, total_mv=200.0)

    assert len(rows) == 1
    assert rows[0].bucket == "EQUITY"
    assert rows[0].value == 20.0


class _CoreQueryMalformedAllocation:
    def __init__(self, views):
        self._views = views

    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "totals": {"total_market_value_reporting_currency": 250.0},
            "snapshot_metadata": {"position_count": 0},
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"views": self._views}


@pytest.mark.parametrize(
    "views",
    [
        "bad-views",
        [{"dimension": "asset_class", "buckets": "bad-map"}],
        [{"dimension": "asset_class", "buckets": ["bad-list"]}],
    ],
)
@pytest.mark.asyncio
async def test_live_aggregation_handles_malformed_allocation_shapes(views):
    service = AggregationService(
        core_query_client=_CoreQueryMalformedAllocation(views),
        performance_client=_PerformanceOkClient(),
    )
    response = await service.get_portfolio_aggregation_live("P1", "2026-02-24")
    metric_map = {row.metric: row.value for row in response.rows}
    assert metric_map["market_value_base"] == 250.0
    assert metric_map["position_count"] == 0.0


class _CoreQueryMalformedSummary:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {
            "totals": ["bad"],
            "snapshot_metadata": "bad",
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {"views": []}


class _PerformanceMissingYtd:
    async def get_workspace_summary(self, payload: dict[str, object]):
        _ = payload
        return 200, {"results_by_period": {}}


class _CoreQueryInvalidPositionCount(_CoreQueryMalformedSummary):
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {
            "totals": {"total_market_value_reporting_currency": 10.0},
            "snapshot_metadata": {"position_count": "not-int"},
        }


@pytest.mark.asyncio
async def test_live_aggregation_uses_defaults_for_malformed_summary_shapes():
    service = AggregationService(
        core_query_client=_CoreQueryMalformedSummary(),
        performance_client=_PerformanceMissingYtd(),
    )

    response = await service.get_portfolio_aggregation_live("P1", "2026-02-24")

    metric_map = {row.metric: row.value for row in response.rows}
    assert metric_map["market_value_base"] == 1_250_000.0
    assert metric_map["return_ytd_pct"] == 0.0


@pytest.mark.asyncio
async def test_live_aggregation_defaults_invalid_position_count():
    service = AggregationService(
        core_query_client=_CoreQueryInvalidPositionCount(),
        performance_client=_PerformanceOkClient(),
    )

    response = await service.get_portfolio_aggregation_live("P1", "2026-02-24")

    metric_map = {row.metric: row.value for row in response.rows}
    assert metric_map["position_count"] == 0.0


class _CoreQueryNestedInvalidSummary:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {"summary": "invalid"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {"views": []}


class _AggregationServiceWithMalformedFetchedSummary(AggregationService):
    async def _fetch_inputs(self, portfolio_id: str, as_of_date: str):
        _ = portfolio_id, as_of_date
        return {"summary": "invalid", "allocation": {"views": []}}, {}


@pytest.mark.asyncio
async def test_live_aggregation_defaults_when_nested_summary_is_not_a_dict():
    service = AggregationService(
        core_query_client=_CoreQueryNestedInvalidSummary(),
        performance_client=_PerformanceOkClient(),
    )

    response = await service.get_portfolio_aggregation_live("P1", "2026-02-24")

    metric_map = {row.metric: row.value for row in response.rows}
    assert metric_map["market_value_base"] == 1_250_000.0
    assert metric_map["position_count"] == 0.0


@pytest.mark.asyncio
async def test_live_aggregation_defaults_when_fetched_summary_is_not_a_dict():
    service = _AggregationServiceWithMalformedFetchedSummary(
        core_query_client=_CoreQueryOkClient(),
        performance_client=_PerformanceOkClient(),
    )

    response = await service.get_portfolio_aggregation_live("P1", "2026-02-24")

    metric_map = {row.metric: row.value for row in response.rows}
    assert metric_map["market_value_base"] == 1_250_000.0
    assert metric_map["position_count"] == 0.0
