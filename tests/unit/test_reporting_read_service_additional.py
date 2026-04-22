import pytest
from fastapi import HTTPException

from app.services.reporting_read_service import ReportingReadService


class _CoreQuerySnapshotMissing:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"unexpected": "shape"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"unexpected": "shape"}

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"unexpected": "shape"}

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"unexpected": "shape"}


class _CoreQuerySuccessMinimal:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "totals": {
                "total_market_value_reporting_currency": 100.0,
                "cash_balance_reporting_currency": 10.0,
                "invested_market_value_reporting_currency": 90.0,
            },
            "snapshot_metadata": {"snapshot_date": payload.get("as_of_date")},
        }

    async def get_portfolio_detail(
        self,
        portfolio_id: str,
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "base_currency": "USD",
            "open_date": "2025-01-06",
            "risk_exposure": "balanced",
            "investment_time_horizon": "long_term",
            "portfolio_type": "discretionary",
            "objective": "Long-term real wealth growth with controlled income and liquidity.",
            "booking_center_code": "Singapore",
            "client_id": "CIF_SG_000184",
            "is_leverage_allowed": False,
            "advisor_id": "RM_SG_001",
            "status": "active",
            "cost_basis_method": "FIFO",
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "scope": {"portfolio_id": portfolio_id},
            "views": [{"dimension": "asset_class", "buckets": []}],
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
            "total": 2,
            "skip": 0,
            "limit": 500,
            "transactions": [
                {
                    "transaction_id": "TXN-INT-1",
                    "transaction_date": "2026-01-15",
                    "transaction_type": "INTEREST",
                    "net_interest_amount_reporting_currency": 3.0,
                    "gross_transaction_amount_reporting_currency": 3.0,
                },
                {
                    "transaction_id": "TXN-DEP-1",
                    "transaction_date": "2026-02-10",
                    "transaction_type": "DEPOSIT",
                    "gross_transaction_amount_reporting_currency": 5.0,
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
            "total": 1,
            "positions": [
                {
                    "security_id": "EQ-1",
                    "instrument_name": "Equity 1",
                    "asset_class": "EQUITY",
                    "quantity": 2,
                    "market_value_reporting_currency": 90.0,
                    "weight": 0.9,
                    "currency": "USD",
                }
            ],
        }


class _CoreQueryNoActivity(_CoreQuerySuccessMinimal):
    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "reporting_currency": params.get("reporting_currency", "USD"),
            "total": 0,
            "skip": 0,
            "limit": 500,
            "transactions": [],
        }

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "total": 0,
            "positions": [],
        }


class _CoreQueryWithoutPortfolioDetail:
    pass


class _CoreQueryProfileUnavailable(_CoreQuerySuccessMinimal):
    async def get_portfolio_detail(
        self,
        portfolio_id: str,
        correlation_id: str | None = None,
    ):
        return 503, {"detail": "core down"}


class _CoreQueryProfileEmpty(_CoreQuerySuccessMinimal):
    async def get_portfolio_detail(
        self,
        portfolio_id: str,
        correlation_id: str | None = None,
    ):
        return 200, {}


class _CoreQueryProfilePartial(_CoreQuerySuccessMinimal):
    async def get_portfolio_detail(
        self,
        portfolio_id: str,
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "client_id": "CIF-1",
            "advisor_id": "RM-1",
            "portfolio_type": "advisory",
            "is_leverage_allowed": True,
        }


class _PerformanceSuccessEmpty:
    async def get_workspace_summary(self, payload: dict[str, object]):
        return 200, {
            "results_by_period": {
                "YTD": {
                    "portfolio_twr": {
                        "net": {
                            "summary": {
                                "cumulative_return": {"base": 2.1},
                                "annualized_return": {"base": 2.1},
                            },
                            "breakdowns": {
                                "daily": [
                                    {
                                        "period": "2025-01-02",
                                        "period_end": "2025-01-02",
                                        "period_return": {"base": 1.0},
                                    }
                                ]
                            },
                        },
                        "gross": {
                            "summary": {
                                "cumulative_return": {"base": 2.3},
                                "annualized_return": {"base": 2.3},
                            }
                        },
                    },
                    "money_weighted_return": {"start_date": "2025-01-01", "end_date": "2026-02-24"},
                }
            }
        }

    async def get_contribution(self, payload: dict[str, object]):
        return 200, {
            "results_by_period": {
                "YTD": {
                    "total_portfolio_return": 2.1,
                    "total_contribution": 2.1,
                    "position_contributions": [],
                    "summary": {
                        "portfolio_contribution": 2.1,
                        "coverage_mv_pct": 100.0,
                        "weighting_scheme": "BOD",
                    },
                    "levels": [],
                }
            },
            "diagnostics": {"notes": []},
            "audit": {"counts": {"input_positions": 0}},
        }


class _RiskSuccess:
    async def calculate_risk(self, payload: dict[str, object]):
        return 200, {
            "results": {
                "YTD": {
                    "metrics": {
                        "VOLATILITY": {"value": 0.2},
                        "SHARPE": {"value": 0.9},
                        "DRAWDOWN": {"value": -0.05},
                        "VAR": {"value": -0.01},
                    }
                }
            },
            "metadata": {
                "risk_free_context": {
                    "requested": True,
                    "applied": True,
                    "reason": "ZERO_RATE",
                    "periodic_rate": 0.0,
                }
            },
        }


class _CoreQueryPagedTransactions(_CoreQuerySuccessMinimal):
    def __init__(self):
        self.seen_skips: list[int] = []

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, correlation_id
        skip = int(params.get("skip", 0))
        self.seen_skips.append(skip)
        if skip == 0:
            return 200, {
                "total": 2,
                "transactions": [
                    {
                        "transaction_id": "TXN-1",
                        "transaction_date": "2026-01-02",
                        "transaction_type": "DEPOSIT",
                        "gross_transaction_amount_reporting_currency": 10.0,
                    }
                ],
            }
        return 200, {
            "total": 2,
            "transactions": [
                {
                    "transaction_id": "TXN-2",
                    "transaction_date": "2026-01-03",
                    "transaction_type": "WITHDRAWAL",
                    "gross_transaction_amount_reporting_currency": 4.0,
                }
            ],
        }


class _CoreQueryTransactionStatus:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self.payload = payload

    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {
            "portfolio_id": "P1",
            "totals": {},
            "snapshot_metadata": {},
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {"views": []}

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, params, correlation_id
        return self.status_code, self.payload

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        _ = portfolio_id, params, correlation_id
        return 200, {"positions": []}


@pytest.mark.asyncio
async def test_summary_requires_as_of_date():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_summary("P1", {}, None)
    assert exc.value.status_code == 422
    assert "as_of_date" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_summary_includes_default_sections_when_sections_not_list():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = await service.get_portfolio_summary(
        "P1",
        {"as_of_date": "2026-02-24", "sections": "WEALTH"},
        None,
    )
    assert "wealth" in response
    assert "allocation" in response
    assert "incomeSummary" in response
    assert "activitySummary" in response


@pytest.mark.asyncio
async def test_summary_forwards_reporting_currency_to_summary_and_transactions():
    class _CoreQueryCapture(_CoreQuerySuccessMinimal):
        def __init__(self):
            self.summary_payload: dict[str, object] | None = None
            self.transaction_params: dict[str, object] | None = None

        async def get_portfolio_summary(
            self,
            portfolio_id: str,
            payload: dict[str, object],
            correlation_id: str | None = None,
        ):
            self.summary_payload = payload
            return await super().get_portfolio_summary(portfolio_id, payload, correlation_id)

        async def get_portfolio_transactions(
            self,
            portfolio_id: str,
            params: dict[str, object],
            correlation_id: str | None = None,
        ):
            self.transaction_params = params
            return await super().get_portfolio_transactions(portfolio_id, params, correlation_id)

    core_query_client = _CoreQueryCapture()
    service = ReportingReadService(
        core_query_client=core_query_client,
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    response = await service.get_portfolio_summary(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "reporting_currency": "SGD",
            "sections": ["WEALTH", "INCOME"],
        },
        None,
    )

    assert response["wealth"]["total_market_value"] == 100.0
    assert core_query_client.summary_payload == {
        "as_of_date": "2026-02-24",
        "reporting_currency": "SGD",
    }
    assert core_query_client.transaction_params is not None
    assert core_query_client.transaction_params["reporting_currency"] == "SGD"


@pytest.mark.asyncio
async def test_summary_contract_missing_raises_502():
    service = ReportingReadService(
        core_query_client=_CoreQuerySnapshotMissing(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_summary("P1", {"as_of_date": "2026-02-24"}, None)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_summary_rejects_unknown_allocation_dimension():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_summary(
            "P1",
            {
                "as_of_date": "2026-02-24",
                "sections": ["ALLOCATION"],
                "allocation_dimensions": ["CUSIP"],
            },
            None,
        )
    assert exc.value.status_code == 422
    assert "Unsupported allocation dimension" in str(exc.value.detail)


def test_requested_sections_filters_non_string_values():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    sections = service._requested_sections(
        request_payload={"sections": ["overview", 10, None, "performance"]},
        default_sections=["OVERVIEW"],
    )
    assert sections == {"OVERVIEW", "PERFORMANCE"}


def test_map_workspace_performance_handles_non_dict_rows():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    mapped = service._map_workspace_performance({"results_by_period": {"YTD": "bad-row"}})
    assert mapped["summary"]["YTD"]["net_cumulative_return"] is None


def test_allocation_and_position_param_builders_preserve_supported_options():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    allocation = service._build_allocation_request(
        {
            "asOfDate": "2026-02-24",
            "reportingCurrency": "EUR",
            "lookThroughMode": "prefer_look_through",
            "allocationDimensions": ["asset_class", "currency"],
        }
    )
    positions = service._build_position_params(
        {"asOfDate": "2026-02-24", "reportingCurrency": "EUR"}
    )

    assert allocation == {
        "dimensions": ["asset_class", "currency"],
        "as_of_date": "2026-02-24",
        "reporting_currency": "EUR",
        "look_through_mode": "prefer_look_through",
    }
    assert positions == {
        "as_of_date": "2026-02-24",
        "include_projected": "false",
        "reporting_currency": "EUR",
    }


@pytest.mark.asyncio
async def test_client_profile_mapping_marks_missing_source_capability_unavailable():
    service = ReportingReadService(
        core_query_client=_CoreQueryWithoutPortfolioDetail(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    profile = await service._portfolio_client_profile(portfolio_id="P1", correlation_id="CID")

    assert profile["status"] == "unavailable"
    assert profile["reason_code"] == "source_client_does_not_support_portfolio_detail"
    assert "risk_exposure" in profile["missing_fields"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("core_client", "reason_code"),
    [
        (_CoreQueryProfileUnavailable(), "source_unavailable"),
        (_CoreQueryProfileEmpty(), "source_payload_missing"),
    ],
)
async def test_client_profile_mapping_marks_upstream_detail_gaps_unavailable(
    core_client,
    reason_code,
):
    service = ReportingReadService(
        core_query_client=core_client,
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    profile = await service._portfolio_client_profile(portfolio_id="P1", correlation_id=None)

    assert profile["status"] == "unavailable"
    assert profile["reason_code"] == reason_code
    assert profile["source"]["endpoint"] == "/portfolios/P1"


@pytest.mark.asyncio
async def test_client_profile_mapping_keeps_partial_profile_source_backed():
    service = ReportingReadService(
        core_query_client=_CoreQueryProfilePartial(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    profile = await service._portfolio_client_profile(portfolio_id="P1", correlation_id=None)

    assert profile["status"] == "partial"
    assert profile["identity"]["client_id"] == "CIF-1"
    assert profile["mandate_profile"]["is_leverage_allowed"] is True
    assert set(profile["missing_fields"]) == {
        "booking_center_code",
        "objective",
        "risk_exposure",
        "investment_time_horizon",
    }


def test_ai_readiness_downgrades_when_profile_or_key_figures_are_missing():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    no_key_figures = service._ai_readiness({"clientProfile": {"status": "present"}})
    partial_profile = service._ai_readiness(
        {
            "clientProfile": {"status": "partial"},
            "keyFigures": {"portfolio_value": {"total_market_value_reporting_currency": 100}},
            "reviewObservations": [],
        }
    )

    assert no_key_figures["status"] == "not_ready"
    assert no_key_figures["supported_ai_features"][2]["status"] == "not_applicable"
    assert partial_profile["status"] == "partial"
    assert partial_profile["supported_ai_features"][0]["status"] == "partial"


def test_review_helper_branches_for_gold_standard_fields():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    assert service._profile_field_present({"is_leverage_allowed": False}, "is_leverage_allowed")
    assert service._security_id_from_position_id("SEC-1") == "SEC-1"
    assert (
        service._workspace_period_start(
            {"portfolio_twr": {"net": {"breakdowns": {"daily": [{"period_start": "2026-01-01"}]}}}}
        )
        == "2026-01-01"
    )
    assert (
        service._workspace_period_end(
            {"portfolio_twr": {"net": {"breakdowns": {"daily": [{"period_end": "2026-02-24"}]}}}}
        )
        == "2026-02-24"
    )
    assert service._optional_number_raw("not-a-number") is None
    assert service._optional_number_raw({"bad": "shape"}) is None
    assert service._position_unrealized_pnl_pct({"unrealized_pnl_pct": 12.3}) == 12.3

    contribution = service._map_performance_contribution(
        status_code=503,
        payload={"detail": "down"},
    )
    assert contribution["status"] == "unavailable"

    response = {"holdings": {"holdingsByAssetClass": {"Equity": [{"security_id": "EQ-1"}]}}}
    service._enrich_holdings_with_contribution(response=response, contribution=contribution)
    assert "ytd_contribution_pct" not in response["holdings"]["holdingsByAssetClass"]["Equity"][0]

    assert service._coverage_status(1, 2) == "partial"
    assert (
        service._benchmark_comparison_coverage(
            {
                "performance": {
                    "benchmark": {"benchmark_code": "BMK", "comparison_status": "available"}
                }
            }
        )
        == "present"
    )


def test_section_item_mappers_include_contribution_and_income_branches():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    performance_items = service._performance_items(
        {
            "summary": {"YTD": {"net_cumulative_return": 1.2}},
            "contribution": {
                "status": "present",
                "period": "YTD",
                "total_portfolio_return_pct": 1.2,
                "total_contribution_pct": 1.2,
                "top_position_contributors": [{"security_id": "EQ-1"}],
                "hierarchy": [{"level": "asset_class", "name": "Asset Class", "rows": []}],
            },
        }
    )
    income_items = service._income_activity_items(
        {
            "incomeSummary": {"net_amount_reporting_currency": 10},
            "activitySummary": {"ignored": 1, "total_fees": 2},
        }
    )
    allocation_items = service._allocation_items({1: [{"group": "bad"}], "byAssetClass": []})

    assert {item["item_type"] for item in performance_items} >= {
        "performance_period",
        "contribution_summary",
        "position_contribution",
    }
    assert income_items[0]["item_type"] == "income_summary"
    assert income_items[1]["bucket"] == "FEES"
    assert allocation_items == []


@pytest.mark.parametrize(
    "dimensions",
    [
        "asset_class",
        [],
        [""],
    ],
)
def test_allocation_dimensions_rejects_non_empty_string_list_contract(dimensions):
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        service._allocation_dimensions({"allocation_dimensions": dimensions})
    assert exc.value.status_code == 422


def test_map_allocation_views_skips_non_conforming_items_and_uses_default_key():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    mapped = service._map_allocation_views(
        [
            "bad-view",
            {"dimension": "", "buckets": []},
            {
                "dimension": "__",
                "buckets": [{"dimension_value": "Unclassified", "weight": "0.1"}],
            },
        ]
    )

    assert mapped == {
        "views": [
            {
                "group": "Unclassified",
                "weight": 0.1,
                "market_value": 0.0,
                "position_count": None,
            }
        ]
    }


@pytest.mark.asyncio
async def test_review_default_sections_include_all_payload_groups():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = await service.get_portfolio_review("P1", {"as_of_date": "2026-02-24"}, None)
    assert "overview" in response
    assert "allocation" in response
    assert "incomeAndActivity" in response
    assert "holdings" in response
    assert "transactions" in response
    assert "riskAnalytics" in response


@pytest.mark.asyncio
async def test_review_transactions_only_fetches_transactions_without_reuse():
    core_query_client = _CoreQueryPagedTransactions()
    service = ReportingReadService(
        core_query_client=core_query_client,
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["TRANSACTIONS"]},
        None,
    )

    assert [
        row["transaction_id"]
        for row in response["transactions"]["transactionsByAssetClass"]["UNKNOWN"]
    ] == [
        "TXN-1",
        "TXN-2",
    ]
    assert [
        row["transaction_id"]
        for row in response["transactions"]["transactionsByCategory"]["Cash Flow"]
    ] == ["TXN-1", "TXN-2"]
    assert core_query_client.seen_skips == [0, 1]


@pytest.mark.asyncio
async def test_review_transactions_are_grouped_for_advisor_review():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    transactions = service._map_review_transactions(
        [
            {
                "transaction_id": "TXN-BUY-1",
                "transaction_date": "2026-01-02",
                "transaction_type": "BUY",
                "asset_class": "Equity",
                "gross_transaction_amount_reporting_currency": -1000,
            },
            {
                "transaction_id": "TXN-CASH-BUY-1",
                "transaction_date": "2026-01-02",
                "transaction_type": "BUY",
                "gross_transaction_amount_reporting_currency": 1000,
            },
            {
                "transaction_id": "TXN-DIV-1",
                "transaction_date": "2026-01-05",
                "transaction_type": "DIVIDEND",
                "net_interest_amount_reporting_currency": 100,
                "withholding_tax_amount_reporting_currency": 15,
            },
        ]
    )

    assert [row["transaction_id"] for row in transactions["transactionsByCategory"]["Trading"]] == [
        "TXN-BUY-1"
    ]
    assert transactions["transactionsByCategory"]["Cash Ledger"][0]["cash_leg"] is True
    assert transactions["transactionsByCategory"]["Cash Ledger"][0]["display_label"] == (
        "Cash ledger leg for Buy"
    )
    income = transactions["transactionsByCategory"]["Income"][0]
    assert income["amount_reporting_currency"] == 100
    assert income["income_or_tax_reporting_currency"] == 85


def test_review_helper_edges_remain_meeting_safe():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    unavailable = service._risk_supportability(results={}, metadata={}, request_payload={})
    assert unavailable["status"] == "unavailable"
    assert unavailable["notes"][0]["code"] == "missing_return_history"

    ready = service._risk_supportability(
        results={"YTD": {"metrics": {}}},
        metadata={"benchmark_context": {"requested": True}},
        request_payload={"benchmark_code": "BMK"},
    )
    assert ready == {"status": "ready", "notes": []}

    supportability = {
        "notes": [
            {"severity": "informational", "code": "info", "message": "Informational note."},
            {"severity": "warning", "code": "warning", "message": "Warning note."},
        ]
    }
    assert service._supportability_reason_code(supportability) == "warning"
    assert service._supportability_message(supportability, "Risk Review") == "Warning note."
    assert service._supportability_reason_code({"notes": [{"severity": "warning"}]}) == (
        "source_unavailable"
    )
    assert service._supportability_message({"notes": [{"severity": "warning"}]}, "Risk Review") == (
        "Risk Review is unavailable for this request."
    )

    assert (
        service._workspace_period_start({"money_weighted_return": {"start_date": "2026-01-01"}})
        == "2026-01-01"
    )
    assert (
        service._workspace_period_end({"money_weighted_return": {"end_date": "2026-02-24"}})
        == "2026-02-24"
    )

    assert (
        service._transaction_review_category(transaction_type="FEE", cash_leg=False, asset_class="")
        == "Fees And Taxes"
    )
    assert (
        service._transaction_review_category(
            transaction_type="TRANSFER_OUT", cash_leg=False, asset_class=""
        )
        == "Cash Flow"
    )
    assert (
        service._transaction_review_category(
            transaction_type="UNKNOWN", cash_leg=False, asset_class="Alternatives"
        )
        == "Alternatives"
    )
    assert service._transaction_display_label("", cash_leg=False) == "Transaction"
    assert service._section_items("custom_section", {"key": "value"}) == [
        {"item_type": "section_payload", "payload": {"key": "value"}}
    ]


def test_review_key_figures_capture_missing_gold_standard_figures():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = {
        "reportingCurrency": "USD",
        "readiness": {"status": "partial"},
        "overview": {
            "total_market_value": 1000,
            "total_cash": 100,
            "invested_market_value": 900,
            "currency": "USD",
        },
        "allocation": {
            "byAssetClass": [{"group": "Equity", "weight": 0.6, "market_value": 600}],
            "byCurrency": [{"group": "USD", "weight": 1.0, "market_value": 1000}],
        },
        "performance": {
            "summary": {
                "YTD": {"net_cumulative_return": -2.5},
                "SI": {"net_cumulative_return": 8.5},
            },
            "benchmark": {
                "benchmark_code": "BMK",
                "comparison_status": "unavailable",
            },
        },
        "riskAnalytics": {
            "summary": {"YTD": {"volatility": 3, "drawdown": -4, "value_at_risk": -1}},
            "results": {
                "YTD": {
                    "metrics": {
                        "VAR": {
                            "details": {
                                "expected_shortfall": -2,
                            }
                        }
                    }
                }
            },
            "supportability": {
                "notes": [
                    {
                        "code": "missing_benchmark",
                        "severity": "warning",
                        "message": "Benchmark-relative risk is unavailable.",
                    }
                ]
            },
        },
        "holdings": {
            "positionCount": 2,
            "holdingsByAssetClass": {
                "Equity": [
                    {
                        "security_id": "EQ-1",
                        "instrument_name": "Equity",
                        "market_value_reporting_currency": 600,
                        "weight": 0.6,
                        "currency": "USD",
                    }
                ],
                "Cash": [
                    {
                        "security_id": "CASH-NEG",
                        "instrument_name": "Negative Cash",
                        "market_value_reporting_currency": -50,
                        "weight": -0.05,
                        "currency": "USD",
                    }
                ],
            },
        },
        "transactions": {
            "transactionCount": 2,
            "transactionsByCategory": {
                "Trading": [{"transaction_id": "T1"}],
                "Cash Ledger": [{"transaction_id": "T2"}],
            },
        },
    }

    response["keyFigures"] = service._review_key_figures(response)
    observations = service._review_observations(response)
    coverage = service._review_report_coverage(response)

    assert response["keyFigures"]["risk"]["ytd_expected_shortfall_pct"] == -2
    assert response["keyFigures"]["holdings"]["negative_cash_position_count"] == 1
    assert response["keyFigures"]["transactions"]["transaction_count_by_category"] == {
        "Trading": 1,
        "Cash Ledger": 1,
    }
    assert {item["observation_id"] for item in observations} == {
        "benchmark_comparison_not_sourced",
        "position_unrealized_pnl_incomplete",
        "negative_ytd_performance",
        "negative_cash_position",
        "top_five_positive_exposure",
        "missing_benchmark",
        "suitability_and_mandate_controls_not_sourced",
    }
    coverage_by_group = {group["group_id"]: group["status"] for group in coverage["figure_groups"]}
    assert coverage_by_group["client_profile"] == "unavailable"
    assert coverage_by_group["benchmark_comparison"] == "partial"
    assert coverage_by_group["position_pnl_and_cost_basis"] == "not_sourced"
    assert coverage_by_group["performance_contribution"] == "unavailable"
    assert coverage_by_group["tax_lot_and_realized_gain_loss"] == "not_sourced"


@pytest.mark.asyncio
async def test_review_without_risk_section_omits_risk_block():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["overview", "holdings"]},
        None,
    )
    assert "overview" in response
    assert "holdings" in response
    assert "riskAnalytics" not in response
    statuses = {section["section_id"]: section["status"] for section in response["client_sections"]}
    assert statuses["executive_summary"] == "ready"
    assert statuses["holdings_appendix"] == "ready"
    assert statuses["risk_review"] == "omitted_by_request"
    assert statuses["performance_review"] == "omitted_by_request"


@pytest.mark.asyncio
async def test_review_section_envelope_marks_unrequested_sections_explicitly():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["OVERVIEW"]},
        None,
    )

    sections = {section["section_id"]: section for section in response["client_sections"]}
    assert sections["executive_summary"]["status"] == "ready"
    assert sections["asset_allocation"] == {
        "section_id": "asset_allocation",
        "title": "Asset Allocation And Portfolio Construction",
        "status": "omitted_by_request",
        "reason_code": "section_not_requested",
        "message": "Asset Allocation And Portfolio Construction was not requested.",
        "items": [],
    }
    advisor_sections = response["advisor_sections"]
    assert len(advisor_sections) == 1
    assert advisor_sections[0]["section_id"] == "advisor_discussion"
    advisor_items = advisor_sections[0]["items"]
    assert {item["prompt_id"] for item in advisor_items} == {
        "review_readiness",
        "portfolio_construction_review",
    }
    assert all(item["advisor_only"] is True for item in advisor_items)
    assert all(
        route_target["mutation_allowed"] is False
        for item in advisor_items
        for route_target in item["route_targets"]
    )
    assert all("advisor_only" not in section for section in response["client_sections"])


@pytest.mark.asyncio
async def test_review_marks_empty_supporting_sections_not_applicable():
    service = ReportingReadService(
        core_query_client=_CoreQueryNoActivity(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    response = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "sections": ["INCOME_AND_ACTIVITY", "HOLDINGS", "TRANSACTIONS"],
        },
        None,
    )

    sections = {section["section_id"]: section for section in response["client_sections"]}
    for section_id in ("income_cash_activity", "holdings_appendix", "transactions_appendix"):
        assert sections[section_id]["status"] == "not_applicable"
        assert sections[section_id]["reason_code"] == "no_applicable_activity"
        assert sections[section_id]["items"]
    assert response["readiness"] == {"status": "ready"}
    source_ref_ids = {
        source_ref["section_id"] for source_ref in response["evidence"]["source_refs"]
    }
    assert {
        "income_cash_activity",
        "holdings_appendix",
        "transactions_appendix",
    } <= source_ref_ids


@pytest.mark.asyncio
async def test_summary_with_explicit_sections_can_exclude_wealth_and_allocation():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = await service.get_portfolio_summary(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["pnl"]},
        None,
    )
    assert response["pnlSummary"]["total_pnl"] == 10.0
    assert "wealth" not in response
    assert "allocation" not in response


def test_to_float_returns_zero_for_non_numeric_string():
    assert ReportingReadService._to_float("not-a-number") == 0.0


def test_to_float_accepts_numeric_values():
    assert ReportingReadService._to_float(7) == 7.0


def test_to_float_returns_zero_for_unsupported_type():
    assert ReportingReadService._to_float(object()) == 0.0


def test_to_int_accepts_float_and_string_variants():
    assert ReportingReadService._to_int(7.9) == 7
    assert ReportingReadService._to_int("8") == 8
    assert ReportingReadService._to_int("bad") == 0
    assert ReportingReadService._to_int(object()) == 0


def test_as_list_and_safe_str_fallbacks():
    assert ReportingReadService._as_list("bad") == []
    assert ReportingReadService._safe_str(123) == ""


def test_activity_and_income_amount_helpers_cover_reporting_fallbacks():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    fee_row = {
        "transaction_type": "FEE",
        "gross_transaction_amount": "-5.00",
        "trade_fee": "-2.50",
    }
    interest_row = {
        "transaction_type": "INTEREST",
        "gross_transaction_amount": "10.00",
        "withholding_tax_amount": "1.00",
        "other_interest_deductions_amount": "2.00",
        "trade_fee": "0.50",
    }

    assert service._activity_bucket_name("WITHDRAWAL") == "OUTFLOWS"
    assert service._activity_bucket_name("FEE") == "FEES"
    assert service._activity_bucket_name("TAX") == "TAXES"
    assert service._activity_reporting_amount(fee_row) == 7.5
    assert service._income_net_reporting_amount(interest_row) == 6.5


@pytest.mark.asyncio
async def test_list_transaction_rows_rejects_missing_transaction_shape():
    service = ReportingReadService(
        core_query_client=_CoreQueryTransactionStatus(200, {"total": 0}),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service._list_transaction_rows(
            portfolio_id="P1",
            correlation_id=None,
            params={"limit": 500},
        )
    assert exc.value.status_code == 502


@pytest.mark.parametrize(
    ("status_code", "expected_status"),
    [
        (404, 404),
        (422, 422),
        (503, 502),
    ],
)
@pytest.mark.asyncio
async def test_list_transaction_rows_maps_core_errors(status_code, expected_status):
    service = ReportingReadService(
        core_query_client=_CoreQueryTransactionStatus(status_code, {"detail": "bad upstream"}),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service._list_transaction_rows(
            portfolio_id="P1",
            correlation_id=None,
            params={"limit": 500},
        )
    assert exc.value.status_code == expected_status


@pytest.mark.parametrize(
    ("method_name", "status_code", "payload", "expected_status"),
    [
        ("_unwrap_core_query_allocation", 200, {"unexpected": "shape"}, 502),
        ("_unwrap_core_query_allocation", 404, {"detail": "missing"}, 404),
        ("_unwrap_core_query_allocation", 422, {"detail": "bad request"}, 422),
        ("_unwrap_core_query_allocation", 503, {"detail": "down"}, 502),
        ("_unwrap_core_query_positions", 200, {"unexpected": "shape"}, 502),
        ("_unwrap_core_query_positions", 404, {"detail": "missing"}, 404),
        ("_unwrap_core_query_positions", 503, {"detail": "down"}, 502),
    ],
)
def test_core_unwrap_helpers_map_invalid_and_error_payloads(
    method_name, status_code, payload, expected_status
):
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    method = getattr(service, method_name)
    with pytest.raises(HTTPException) as exc:
        method(status_code=status_code, payload=payload)
    assert exc.value.status_code == expected_status


class _PerformanceWorkspaceStatusError(_PerformanceSuccessEmpty):
    async def get_workspace_summary(self, payload: dict[str, object]):
        return 500, {"detail": "twr failed"}


class _PerformanceWorkspaceNoReturns(_PerformanceSuccessEmpty):
    async def get_workspace_summary(self, payload: dict[str, object]):
        return 200, {
            "results_by_period": {"YTD": {"portfolio_twr": {"net": {"breakdowns": {"daily": []}}}}}
        }


class _RiskStatusError(_RiskSuccess):
    async def calculate_risk(self, payload: dict[str, object]):
        return 500, {"detail": "risk failed"}


@pytest.mark.asyncio
async def test_build_risk_analytics_reports_workspace_summary_failure():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceWorkspaceStatusError(),
        risk_client=_RiskSuccess(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24", {})
    assert result["supportability"]["status"] == "unavailable"
    assert result["supportability"]["notes"][0]["code"] == "risk_return_history_unavailable"


@pytest.mark.asyncio
async def test_build_risk_analytics_reports_empty_daily_returns():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceWorkspaceNoReturns(),
        risk_client=_RiskSuccess(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24", {})
    assert result["supportability"]["status"] == "unavailable"
    assert result["supportability"]["notes"][0]["code"] == "missing_return_history"


@pytest.mark.asyncio
async def test_build_risk_analytics_reports_risk_call_failure():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskStatusError(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24", {})
    assert result["supportability"]["status"] == "unavailable"
    assert result["supportability"]["notes"][0]["code"] == "risk_upstream_failure"


def test_extract_daily_returns_skips_invalid_items():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    workspace_payload = {
        "results_by_period": {
            "YTD": {
                "portfolio_twr": {
                    "net": {
                        "breakdowns": {
                            "daily": [
                                "bad",
                                {"period": "2025-01-03", "period_return": {"base": "bad"}},
                                {"period": 123, "period_return": {"base": 1.2}},
                                {
                                    "period": "2025-01-04",
                                    "period_end": "2025-01-04",
                                    "period_return": {"base": 0.4},
                                },
                            ]
                        }
                    }
                }
            }
        }
    }
    returns = service._extract_daily_returns_from_workspace_summary(workspace_payload)
    assert returns == [{"date": "2025-01-04", "value": 0.4}]


def test_extract_daily_returns_falls_back_to_period_prefix():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    workspace_payload = {
        "results_by_period": {
            "YTD": {
                "portfolio_twr": {
                    "net": {
                        "breakdowns": {
                            "daily": [
                                {
                                    "period": "2025-01-03T00:00:00Z",
                                    "period_return": {"base": 0.6},
                                }
                            ]
                        }
                    }
                }
            }
        }
    }

    returns = service._extract_daily_returns_from_workspace_summary(workspace_payload)

    assert returns == [{"date": "2025-01-03", "value": 0.6}]


def test_workspace_period_helpers_fall_back_to_money_weighted_return_dates():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    period_payload = {
        "money_weighted_return": {
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
        }
    }

    assert service._workspace_period_start(period_payload) == "2025-01-01"
    assert service._workspace_period_end(period_payload) == "2025-12-31"


def test_workspace_portfolio_open_date_returns_none_when_no_supported_start_exists():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    assert service._workspace_portfolio_open_date({"results_by_period": {"YTD": {}}}) is None


def test_return_base_handles_string_and_invalid_string_values():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    assert service._return_base({"alpha": {"base": "3.25"}}, "alpha") == 3.25
    assert service._return_base({"alpha": {"base": "not-a-number"}}, "alpha") is None


def test_build_workspace_summary_request_sets_reporting_currency_aliases():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    request = service._build_workspace_summary_request(
        portfolio_id="P1",
        as_of_date="2026-02-24",
        request_payload={"reportingCurrency": "SGD"},
        periods=["YTD"],
    )

    assert request["report_ccy"] == "SGD"
    assert request["currency"] == "SGD"


@pytest.mark.asyncio
async def test_build_risk_analytics_reports_missing_portfolio_open_date():
    class _PerformanceWorkspaceNoOpenDate:
        async def get_workspace_summary(self, payload: dict[str, object]):
            _ = payload
            return 200, {
                "results_by_period": {
                    "YTD": {
                        "portfolio_twr": {
                            "net": {
                                "breakdowns": {
                                    "daily": [
                                        {
                                            "period_end": "2026-02-24",
                                            "period_return": {"base": 0.4},
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            }

    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceWorkspaceNoOpenDate(),
        risk_client=_RiskSuccess(),
    )

    result = await service._build_risk_analytics("P1", "2026-02-24", {})

    assert result["supportability"]["status"] == "unavailable"
    assert result["supportability"]["notes"][0]["code"] == "missing_return_history"


@pytest.mark.asyncio
async def test_build_risk_analytics_surfaces_missing_risk_free_and_benchmark_notes():
    service = ReportingReadService(
        core_query_client=_CoreQuerySuccessMinimal(),
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )

    result = await service._build_risk_analytics("P1", "2026-02-24", {})

    notes = {note["code"]: note for note in result["supportability"]["notes"]}
    assert result["supportability"]["status"] == "ready"
    assert notes["missing_risk_free_rate"]["severity"] == "informational"
    assert notes["missing_benchmark"]["severity"] == "informational"
    assert result["source"] == {
        "service": "lotus-risk",
        "endpoint": "/analytics/risk/calculate",
    }
    assert result["summary"]["YTD"] == {
        "volatility": 0.2,
        "risk_adjusted_return": 0.9,
        "drawdown": -0.05,
        "value_at_risk": -0.01,
    }
