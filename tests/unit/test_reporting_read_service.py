import pytest
from fastapi import HTTPException

from app.services.reporting_read_service import ReportingReadService


class _CoreQueryClientSuccess:
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


class _PerformanceClientSuccess:
    async def get_workspace_summary(self, payload: dict[str, object]):
        return 200, {
            "results_by_period": {
                "MTD": {
                    "portfolio_twr": {
                        "net": {
                            "summary": {
                                "cumulative_return": {"base": 1.1},
                                "annualized_return": {"base": 1.1},
                            },
                            "breakdowns": {
                                "daily": [
                                    {
                                        "period": "2026-02-24",
                                        "period_end": "2026-02-24",
                                        "period_return": {"base": 0.1},
                                    }
                                ]
                            },
                        },
                        "gross": {
                            "summary": {
                                "cumulative_return": {"base": 1.2},
                                "annualized_return": {"base": 1.2},
                            }
                        },
                    },
                    "money_weighted_return": {"start_date": "2026-02-01", "end_date": "2026-02-24"},
                },
                "YTD": {
                    "portfolio_twr": {
                        "net": {
                            "summary": {
                                "cumulative_return": {"base": 4.1},
                                "annualized_return": {"base": 4.1},
                            },
                            "breakdowns": {
                                "daily": [
                                    {
                                        "period": "2026-02-24",
                                        "period_end": "2026-02-24",
                                        "period_return": {"base": 1.0},
                                    }
                                ]
                            },
                        },
                        "gross": {
                            "summary": {
                                "cumulative_return": {"base": 4.3},
                                "annualized_return": {"base": 4.3},
                            }
                        },
                    },
                    "money_weighted_return": {"start_date": "2026-01-01", "end_date": "2026-02-24"},
                },
                "THREE_YEAR": {
                    "portfolio_twr": {
                        "net": {
                            "summary": {
                                "cumulative_return": {"base": 12.0},
                                "annualized_return": {"base": 3.9},
                            },
                            "breakdowns": {
                                "daily": [
                                    {
                                        "period": "2026-02-24",
                                        "period_end": "2026-02-24",
                                        "period_return": {"base": 1.0},
                                    }
                                ]
                            },
                        },
                        "gross": {
                            "summary": {
                                "cumulative_return": {"base": 12.5},
                                "annualized_return": {"base": 4.1},
                            }
                        },
                    },
                    "money_weighted_return": {"start_date": "2023-02-24", "end_date": "2026-02-24"},
                },
            }
        }


class _RiskClientSuccess:
    async def calculate_risk(self, payload: dict[str, object]):
        return 200, {
            "results": {
                "YTD": {
                    "startDate": "2025-01-01",
                    "endDate": "2025-02-24",
                    "metrics": {
                        "VOLATILITY": {"value": 0.12},
                        "SHARPE": {"value": 1.05},
                        "DRAWDOWN": {"value": -0.08},
                        "VAR": {"value": -0.02},
                    },
                }
            },
            "metadata": {
                "risk_free_context": {
                    "requested": True,
                    "applied": True,
                    "reason": "ANNUAL_RATE_APPLIED",
                    "periodic_rate": 0.0001,
                }
            },
        }


class _CoreQueryClientNotFound:
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


class _CoreQueryClientFailure:
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


class _PerformanceClientFailure:
    async def get_workspace_summary(self, payload: dict[str, object]):
        return 503, {"detail": "upstream unavailable"}


class _RiskClientFailure:
    async def calculate_risk(self, payload: dict[str, object]):
        return 503, {"detail": "upstream unavailable"}


@pytest.mark.asyncio
async def test_summary_uses_strategic_core_query_routes_for_summary_details():
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
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
    class _CoreQueryClientAllocationCapture(_CoreQueryClientSuccess):
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

    core_query_client = _CoreQueryClientAllocationCapture()
    service = ReportingReadService(
        core_query_client=core_query_client,
        performance_client=_PerformanceClientSuccess(),
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
    assert core_query_client.last_allocation_payload == {
        "as_of_date": "2026-02-24",
        "dimensions": ["asset_class", "region"],
        "look_through_mode": "prefer_look_through",
    }


@pytest.mark.asyncio
async def test_review_composes_core_query_performance_and_risk():
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
            "sections": ["OVERVIEW", "PERFORMANCE", "RISK_ANALYTICS", "HOLDINGS"],
        },
        "CID-1",
    )
    assert response["contract_version"] == "v1"
    assert response["report_id"] == "portfolio-review:P1:2026-02-24"
    assert response["portfolio_id"] == "P1"
    assert response["as_of_date"] == "2026-02-24"
    assert response["readiness"] == {"status": "ready"}
    assert response["methodology"]["benchmark_code"] == "BMK_GLOBAL_BALANCED_60_40"
    assert response["methodology"]["return_methodology"] == "time_weighted_return"
    assert response["evidence"]["product_id"] == "lotus-report:ClientReportEvidencePack:v1"
    assert response["evidence"]["lineage_bundle_id"] == (
        "lineage:lotus-report:portfolio-review:P1:2026-02-24"
    )
    assert response["evidence"]["correlation_id"] == "CID-1"
    assert response["evidence"]["source_services"] == [
        "lotus-core",
        "lotus-performance",
        "lotus-risk",
    ]
    source_refs = {
        source_ref["section_id"]: source_ref for source_ref in response["evidence"]["source_refs"]
    }
    assert source_refs["executive_summary"]["source_service"] == "lotus-core"
    assert source_refs["performance_review"]["source_service"] == "lotus-performance"
    assert source_refs["risk_review"]["source_service"] == "lotus-risk"
    assert response["evidence"]["trust_metadata"]["completeness_status"] == "complete"
    assert response["evidence"]["trust_metadata"]["data_quality_status"] == "quality_passed"
    assert [section["section_id"] for section in response["client_sections"]] == [
        "executive_summary",
        "asset_allocation",
        "performance_review",
        "risk_review",
        "income_cash_activity",
        "holdings_appendix",
        "transactions_appendix",
    ]
    section_statuses = {
        section["section_id"]: section["status"] for section in response["client_sections"]
    }
    assert section_statuses["executive_summary"] == "ready"
    assert section_statuses["asset_allocation"] == "omitted_by_request"
    assert section_statuses["performance_review"] == "ready"
    assert section_statuses["risk_review"] == "ready"
    assert response["advisor_sections"] == []
    assert response["overview"]["total_market_value"] == 1_000_000.0
    assert "YTD" in response["performance"]["summary"]
    assert response["performance"]["benchmark"] == {"benchmark_code": "BMK_GLOBAL_BALANCED_60_40"}
    assert response["performance"]["summary"]["MTD"]["net_annualized_return"] is None
    assert response["performance"]["summary"]["YTD"]["gross_annualized_return"] is None
    assert response["performance"]["summary"]["THREE_YEAR"]["net_annualized_return"] == 3.9
    assert "YTD" in response["riskAnalytics"]["results"]
    assert response["riskAnalytics"]["source"]["service"] == "lotus-risk"
    assert response["riskAnalytics"]["supportability"]["status"] == "ready"
    assert response["riskAnalytics"]["supportability"]["notes"][0]["code"] == "missing_benchmark"
    assert response["riskAnalytics"]["summary"]["YTD"] == {
        "volatility": 0.12,
        "risk_adjusted_return": 1.05,
        "drawdown": -0.08,
        "value_at_risk": -0.02,
    }
    assert response["holdings"]["holdingsByAssetClass"]["Equity"][0]["security_id"] == "EQ-1"


@pytest.mark.asyncio
async def test_review_sets_performance_none_when_performance_unavailable():
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientFailure(),
        risk_client=_RiskClientSuccess(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["PERFORMANCE"]},
        None,
    )
    assert response["performance"] is None
    performance_section = next(
        section
        for section in response["client_sections"]
        if section["section_id"] == "performance_review"
    )
    assert performance_section["status"] == "unavailable"
    assert performance_section["reason_code"] == "source_unavailable"
    assert response["readiness"] == {
        "status": "partial",
        "reason": "Unavailable sections for the selected request: Performance Review",
    }


@pytest.mark.asyncio
async def test_review_sets_risk_unavailable_when_upstreams_fail():
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientFailure(),
        risk_client=_RiskClientFailure(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["RISK_ANALYTICS"]},
        None,
    )
    assert response["riskAnalytics"]["supportability"]["status"] == "unavailable"
    assert response["riskAnalytics"]["supportability"]["notes"][0]["code"] == (
        "risk_return_history_unavailable"
    )
    assert response["evidence"]["trust_metadata"]["completeness_status"] == "partial"
    assert response["evidence"]["trust_metadata"]["data_quality_status"] == "quality_warning"
    risk_section = next(
        section for section in response["client_sections"] if section["section_id"] == "risk_review"
    )
    assert risk_section["status"] == "unavailable"
    assert risk_section["reason_code"] == "risk_return_history_unavailable"
    assert response["readiness"] == {
        "status": "partial",
        "reason": "Unavailable sections for the selected request: Risk Review",
    }


@pytest.mark.asyncio
async def test_core_query_not_found_maps_to_404():
    service = ReportingReadService(
        core_query_client=_CoreQueryClientNotFound(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_summary("P404", {"as_of_date": "2026-02-24"}, None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_core_query_failure_maps_to_502():
    service = ReportingReadService(
        core_query_client=_CoreQueryClientFailure(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_review("P1", {"as_of_date": "2026-02-24"}, None)
    assert exc.value.status_code == 502
