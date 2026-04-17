import pytest
from fastapi import HTTPException

from app.services.reporting_read_service import ReportingReadService


class _PasClientSuccess:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "totals": {
                "total_market_value_reporting_currency": 1_000_000.0,
                "cash_balance_reporting_currency": 50_000.0,
                "invested_market_value_reporting_currency": 998_800.0,
            },
            "snapshot_metadata": {"snapshot_date": payload.get("as_of_date")},
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "scope": {"portfolio_id": portfolio_id},
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {
                            "dimension_value": "Equity",
                            "weight": 0.6,
                            "market_value_reporting_currency": 600000.0,
                            "position_count": 3,
                        }
                    ],
                }
            ],
        }

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "reporting_currency": params.get("reporting_currency", "USD"),
            "total": 3,
            "skip": 0,
            "limit": 500,
            "transactions": [
                {
                    "transaction_id": "TXN-DIV-1",
                    "transaction_date": "2026-01-10",
                    "transaction_type": "DIVIDEND",
                    "gross_transaction_amount_reporting_currency": 100.0,
                    "withholding_tax_amount_reporting_currency": 10.0,
                    "other_interest_deductions_amount_reporting_currency": 0.0,
                },
                {
                    "transaction_id": "TXN-DEP-1",
                    "transaction_date": "2026-02-01",
                    "transaction_type": "DEPOSIT",
                    "gross_transaction_amount_reporting_currency": 1000.0,
                },
                {
                    "transaction_id": "TXN-TAX-1",
                    "transaction_date": "2026-02-05",
                    "transaction_type": "TAX",
                    "withholding_tax_amount_reporting_currency": 0.0,
                },
            ],
        }

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "total": 2,
            "positions": [
                {
                    "security_id": "EQ-1",
                    "instrument_name": "Equity 1",
                    "asset_class": "Equity",
                    "quantity": 10,
                    "market_value_reporting_currency": 600000.0,
                    "weight": 0.6,
                    "currency": "USD",
                },
                {
                    "security_id": "CASH-1",
                    "instrument_name": "Cash",
                    "asset_class": "Cash",
                    "quantity": 1,
                    "market_value_reporting_currency": 50000.0,
                    "weight": 0.05,
                    "currency": "USD",
                },
            ],
        }

    async def get_performance_input(
        self,
        portfolio_id: str,
        as_of_date: str,
        lookback_days: int = 1200,
    ):
        return 200, {
            "portfolioId": portfolio_id,
            "baseCurrency": "USD",
            "performanceStartDate": "2025-01-01",
            "valuationPoints": [
                {
                    "day": 1,
                    "perf_date": "2025-01-02",
                    "begin_mv": 100.0,
                    "end_mv": 101.0,
                    "bod_cf": 0.0,
                    "eod_cf": 0.0,
                    "mgmt_fees": 0.0,
                }
            ],
        }


class _PaClientSuccess:
    async def get_pas_input_twr(self, portfolio_id: str, as_of_date: str, periods: list[str]):
        return 200, {
            "resultsByPeriod": {
                "YTD": {
                    "net_cumulative_return": 4.1,
                    "net_annualized_return": 4.1,
                    "gross_cumulative_return": 4.3,
                    "gross_annualized_return": 4.3,
                }
            }
        }

    async def calculate_twr(self, payload: dict[str, object]):
        return 200, {
            "results_by_period": {
                "EXPLICIT": {
                    "breakdowns": {
                        "daily": [{"period": "2025-01-02", "summary": {"period_return_pct": 1.0}}]
                    }
                }
            }
        }


class _RiskClientSuccess:
    async def calculate_risk(self, payload: dict[str, object]):
        return 200, {
            "results": {
                "YTD": {
                    "startDate": "2025-01-01",
                    "endDate": "2025-02-24",
                    "metrics": {"VOLATILITY": {"value": 0.12}},
                }
            }
        }


class _PasClientNotFound:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 404, {"detail": "Portfolio not found"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 404, {"detail": "Portfolio not found"}

    async def get_performance_input(
        self,
        portfolio_id: str,
        as_of_date: str,
        lookback_days: int = 1200,
    ):
        return 404, {"detail": "Portfolio not found"}

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 404, {"detail": "Portfolio not found"}

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 404, {"detail": "Portfolio not found"}


class _PasClientFailure:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 503, {"detail": "upstream unavailable"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 503, {"detail": "upstream unavailable"}

    async def get_performance_input(
        self,
        portfolio_id: str,
        as_of_date: str,
        lookback_days: int = 1200,
    ):
        return 503, {"detail": "upstream unavailable"}

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 503, {"detail": "upstream unavailable"}

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 503, {"detail": "upstream unavailable"}


class _PaClientFailure:
    async def get_pas_input_twr(self, portfolio_id: str, as_of_date: str, periods: list[str]):
        return 503, {"detail": "upstream unavailable"}

    async def calculate_twr(self, payload: dict[str, object]):
        return 503, {"detail": "upstream unavailable"}


class _RiskClientFailure:
    async def calculate_risk(self, payload: dict[str, object]):
        return 503, {"detail": "upstream unavailable"}


@pytest.mark.asyncio
async def test_summary_uses_strategic_pas_routes_for_summary_details():
    service = ReportingReadService(
        pas_client=_PasClientSuccess(),
        pa_client=_PaClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    response = await service.get_portfolio_summary(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "sections": ["WEALTH", "ALLOCATION", "PNL", "INCOME", "ACTIVITY"],
        },
        "CID-1",
    )
    assert response["scope"]["portfolio_id"] == "P1"
    assert response["wealth"]["total_market_value"] == 1_000_000.0
    assert response["wealth"]["total_cash"] == 50_000.0
    assert response["allocation"]["byAssetClass"][0]["group"] == "Equity"
    assert response["allocation"]["byAssetClass"][0]["market_value"] == 600000.0
    assert response["incomeSummary"]["net_amount_reporting_currency"] == 90.0
    assert response["activitySummary"]["total_inflows"] == 1000.0
    assert response["pnlSummary"]["total_pnl"] == 1_200.0


@pytest.mark.asyncio
async def test_summary_honors_requested_allocation_dimensions():
    class _PasClientAllocationCapture(_PasClientSuccess):
        def __init__(self):
            self.last_allocation_payload: dict[str, object] | None = None

        async def get_asset_allocation(
            self,
            portfolio_id: str,
            payload: dict[str, object],
            correlation_id: str | None = None,
        ):
            self.last_allocation_payload = payload
            return 200, {
                "scope": {"portfolio_id": portfolio_id},
                "views": [
                    {
                        "dimension": "asset_class",
                        "buckets": [
                            {
                                "dimension_value": "Equity",
                                "weight": 0.6,
                                "market_value_reporting_currency": 600000.0,
                                "position_count": 3,
                            }
                        ],
                    },
                    {
                        "dimension": "region",
                        "buckets": [
                            {
                                "dimension_value": "North America",
                                "weight": 0.6,
                                "market_value_reporting_currency": 600000.0,
                                "position_count": 3,
                            }
                        ],
                    },
                ],
            }

    pas_client = _PasClientAllocationCapture()
    service = ReportingReadService(
        pas_client=pas_client,
        pa_client=_PaClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )

    response = await service.get_portfolio_summary(
        portfolio_id="P1",
        request_payload={
            "as_of_date": "2026-02-24",
            "sections": ["ALLOCATION"],
            "allocation_dimensions": ["ASSET_CLASS", "REGION"],
            "look_through_mode": "prefer_look_through",
        },
        correlation_id="corr-6",
    )

    assert "wealth" not in response
    assert "byAssetClass" in response["allocation"]
    assert "byRegion" in response["allocation"]
    assert pas_client.last_allocation_payload == {
        "as_of_date": "2026-02-24",
        "dimensions": ["asset_class", "region"],
        "look_through_mode": "prefer_look_through",
    }


@pytest.mark.asyncio
async def test_review_composes_pas_pa_and_risk():
    service = ReportingReadService(
        pas_client=_PasClientSuccess(),
        pa_client=_PaClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "sections": ["OVERVIEW", "PERFORMANCE", "RISK_ANALYTICS", "HOLDINGS"],
        },
        "CID-1",
    )
    assert response["portfolio_id"] == "P1"
    assert response["overview"]["total_market_value"] == 1_000_000.0
    assert "YTD" in response["performance"]["summary"]
    assert "YTD" in response["riskAnalytics"]["results"]
    assert response["holdings"]["holdingsByAssetClass"]["Equity"][0]["security_id"] == "EQ-1"


@pytest.mark.asyncio
async def test_review_sets_performance_none_when_pa_unavailable():
    service = ReportingReadService(
        pas_client=_PasClientSuccess(),
        pa_client=_PaClientFailure(),
        risk_client=_RiskClientSuccess(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["PERFORMANCE"]},
        None,
    )
    assert response["performance"] is None


@pytest.mark.asyncio
async def test_review_sets_risk_none_when_upstreams_fail():
    service = ReportingReadService(
        pas_client=_PasClientSuccess(),
        pa_client=_PaClientFailure(),
        risk_client=_RiskClientFailure(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["RISK_ANALYTICS"]},
        None,
    )
    assert response["riskAnalytics"] is None


@pytest.mark.asyncio
async def test_pas_not_found_maps_to_404():
    service = ReportingReadService(
        pas_client=_PasClientNotFound(),
        pa_client=_PaClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_summary("P404", {"as_of_date": "2026-02-24"}, None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_pas_failure_maps_to_502():
    service = ReportingReadService(
        pas_client=_PasClientFailure(),
        pa_client=_PaClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_review("P1", {"as_of_date": "2026-02-24"}, None)
    assert exc.value.status_code == 502
