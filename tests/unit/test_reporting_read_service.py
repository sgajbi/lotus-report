import pytest
from fastapi import HTTPException

from app.services.reporting_read_service import ReportingReadService

FORBIDDEN_ADVISOR_PROMPT_WORDS = {"create", "creates", "approve", "approves", "mutate", "mutates"}


def _advisor_prompt_items(response: dict[str, object]) -> list[dict[str, object]]:
    advisor_sections = response["advisor_sections"]
    assert isinstance(advisor_sections, list)
    assert len(advisor_sections) == 1
    advisor_section = advisor_sections[0]
    assert advisor_section["section_id"] == "advisor_discussion"
    assert advisor_section["status"] == "ready"
    items = advisor_section["items"]
    assert isinstance(items, list)
    return items


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
            "total": 4,
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
                    "transaction_id": "TXN-SELL-1",
                    "transaction_date": "2026-02-03",
                    "transaction_type": "SELL",
                    "instrument_id": "I-EQ-1",
                    "security_id": "EQ-1",
                    "gross_transaction_amount_reporting_currency": 25000.0,
                    "realized_gain_loss_reporting_currency": 1250.0,
                    "realized_gain_loss_local": 1250.0,
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
                    "isin": "US0000000001",
                    "sector": "Technology",
                    "country_of_risk": "United States",
                    "product_type": "Equity",
                    "liquidity_tier": "L1",
                    "held_since_date": "2025-01-15",
                    "quantity": 10,
                    "cost_basis": "500000.0",
                    "valuation": {
                        "market_price": "60.0",
                        "market_value": "600000.0",
                        "unrealized_gain_loss": "100000.0",
                        "market_value_local": "600000.0",
                        "unrealized_gain_loss_local": "100000.0",
                    },
                    "weight": 0.6,
                    "currency": "USD",
                },
                {
                    "security_id": "CASH-1",
                    "instrument_name": "Cash",
                    "asset_class": "Cash",
                    "isin": "CASH-USD",
                    "product_type": "Cash",
                    "liquidity_tier": "L1",
                    "quantity": 1,
                    "cost_basis": 50000.0,
                    "market_value_reporting_currency": 50000.0,
                    "valuation": {"unrealized_gain_loss": 0.0},
                    "weight": 0.05,
                    "currency": "USD",
                },
            ],
        }


class _PerformanceClientSuccess:
    def __init__(self):
        self.seen_payloads: list[dict[str, object]] = []

    async def get_workspace_summary(self, payload: dict[str, object]):
        self.seen_payloads.append(payload)
        return 200, {
            "results_by_period": {
                "1M": {
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
                    "benchmark": {
                        "summary": {
                            "cumulative_return": {"base": 0.9},
                            "annualized_return": {"base": 0.9},
                        },
                        "benchmark_id": "BMK_GLOBAL_BALANCED_60_40",
                        "benchmark_currency": "USD",
                        "input_mode": "stateful",
                        "return_source": "calculated",
                    },
                    "active": {
                        "net": {
                            "cumulative_return": {"base": 0.2},
                            "annualized_return": {"base": 0.2},
                        }
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
                    "benchmark": {
                        "summary": {
                            "cumulative_return": {"base": 3.4},
                            "annualized_return": {"base": 3.4},
                        },
                        "benchmark_id": "BMK_GLOBAL_BALANCED_60_40",
                        "benchmark_currency": "USD",
                        "input_mode": "stateful",
                        "return_source": "calculated",
                    },
                    "active": {
                        "net": {
                            "cumulative_return": {"base": 0.7},
                            "annualized_return": {"base": 0.7},
                        }
                    },
                    "money_weighted_return": {"start_date": "2026-01-01", "end_date": "2026-02-24"},
                },
                "5Y": {
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
                    "benchmark": {
                        "summary": {
                            "cumulative_return": {"base": 10.8},
                            "annualized_return": {"base": 3.4},
                        },
                        "benchmark_id": "BMK_GLOBAL_BALANCED_60_40",
                        "benchmark_currency": "USD",
                        "input_mode": "stateful",
                        "return_source": "calculated",
                    },
                    "active": {
                        "net": {
                            "cumulative_return": {"base": 1.2},
                            "annualized_return": {"base": 0.5},
                        }
                    },
                    "money_weighted_return": {"start_date": "2023-02-24", "end_date": "2026-02-24"},
                },
            }
        }

    async def get_contribution(self, payload: dict[str, object]):
        self.seen_payloads.append(payload)
        return 200, {
            "results_by_period": {
                "YTD": {
                    "total_portfolio_return": 4.1,
                    "total_contribution": 4.1,
                    "position_contributions": [
                        {
                            "position_id": "P1:EQ-1",
                            "total_contribution": 3.5,
                            "average_weight": 60.0,
                            "total_return": 5.8,
                        },
                        {
                            "position_id": "P1:CASH-1",
                            "total_contribution": 0.0,
                            "average_weight": 5.0,
                            "total_return": 0.0,
                        },
                    ],
                    "summary": {
                        "portfolio_contribution": 4.1,
                        "coverage_mv_pct": 100.0,
                        "weighting_scheme": "BOD",
                    },
                    "levels": [
                        {
                            "level": 1,
                            "name": "asset_class",
                            "rows": [
                                {
                                    "key": {"asset_class": "Equity"},
                                    "contribution": 3.5,
                                    "weight_avg": 60.0,
                                    "is_other": False,
                                }
                            ],
                        }
                    ],
                }
            },
            "diagnostics": {"notes": []},
            "audit": {"counts": {"input_positions": 2}},
        }


class _RiskClientSuccess:
    def __init__(self):
        self.seen_payloads: list[dict[str, object]] = []

    async def calculate_risk(self, payload: dict[str, object]):
        self.seen_payloads.append(payload)
        return 200, {
            "results": {
                "YTD": {
                    "start_date": "2025-01-01",
                    "end_date": "2025-02-24",
                    "metrics": {
                        "VOLATILITY": {"value": 0.12},
                        "SHARPE": {"value": 1.05},
                        "DRAWDOWN": {"value": -0.08},
                        "VAR": {"value": -0.02},
                        "BETA": {"value": 0.82},
                        "TRACKING_ERROR": {"value": 0.04},
                        "INFORMATION_RATIO": {"value": 0.72},
                    },
                }
            },
            "metadata": {
                "risk_free_context": {
                    "requested": True,
                    "applied": True,
                    "reason": "ANNUAL_RATE_APPLIED",
                    "periodic_rate": 0.0001,
                },
                "benchmark_context": {
                    "requested": True,
                    "requested_metrics": ["BETA", "TRACKING_ERROR", "INFORMATION_RATIO"],
                },
            },
        }


class _RiskClientPeriodFallback:
    def __init__(self):
        self.seen_payloads: list[dict[str, object]] = []

    async def calculate_risk(self, payload: dict[str, object]):
        self.seen_payloads.append(payload)
        stateful_input = payload["stateful_input"]
        assert isinstance(stateful_input, dict)
        periods = stateful_input["periods"]
        assert isinstance(periods, list)
        if len(periods) > 1:
            return 424, {
                "error": {
                    "message": (
                        "Benchmark composition window does not cover requested date "
                        "2023-04-12 for benchmark_id=BMK_PB_GLOBAL_BALANCED_60_40."
                    )
                }
            }
        period = periods[0]
        assert isinstance(period, dict)
        if period.get("name") == "THREE_YEAR":
            return 424, {
                "error": {
                    "message": (
                        "Benchmark composition window does not cover requested date "
                        "2023-04-12 for benchmark_id=BMK_PB_GLOBAL_BALANCED_60_40."
                    )
                }
            }
        return 200, {
            "results": {
                "YTD": {
                    "start_date": "2026-01-01",
                    "end_date": "2026-02-24",
                    "metrics": {
                        "VOLATILITY": {"value": 0.12},
                        "BETA": {"value": 0.82},
                        "TRACKING_ERROR": {"value": 0.04},
                        "INFORMATION_RATIO": {"value": 0.72},
                    },
                }
            },
            "metadata": {
                "benchmark_context": {
                    "requested": True,
                    "requested_metrics": ["BETA", "TRACKING_ERROR", "INFORMATION_RATIO"],
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

    async def get_portfolio_detail(
        self,
        portfolio_id: str,
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

    async def get_portfolio_detail(
        self,
        portfolio_id: str,
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

    async def get_contribution(self, payload: dict[str, object]):
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
    performance_client = _PerformanceClientSuccess()
    risk_client = _RiskClientSuccess()
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=performance_client,
        risk_client=risk_client,
    )
    response = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "reporting_currency": "USD",
            "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
            "sections": [
                "OVERVIEW",
                "ALLOCATION",
                "PERFORMANCE",
                "RISK_ANALYTICS",
                "INCOME_AND_ACTIVITY",
                "HOLDINGS",
                "TRANSACTIONS",
            ],
        },
        "CID-1",
    )
    assert response["contract_version"] == "v1"
    assert response["report_id"] == "portfolio-review:P1:2026-02-24"
    assert response["portfolio_id"] == "P1"
    assert response["as_of_date"] == "2026-02-24"
    assert response["reviewPeriod"] == {
        "label": "YTD",
        "start_date": "2026-01-01",
        "end_date": "2026-02-24",
    }
    assert response["reportingCurrency"] == "USD"
    assert response["readiness"] == {"status": "ready"}
    assert response["audience"]["primary"] == "client_advisor"
    assert response["audience"]["client_ready"] is True
    assert response["disclosures"][-1]["disclosure_id"] == "reporting_view"
    assert response["keyFigures"]["conventions"] == {
        "currency": "USD",
        "monetary_fields": "reporting currency amounts use *_reporting_currency names",
        "percentage_fields": "normalized key figure percentages use *_pct names",
        "legacy_weight_fields": "section allocation and holding weights remain decimal ratios",
    }
    assert response["clientProfile"]["status"] == "present"
    assert response["clientProfile"]["identity"] == {
        "client_id": "CIF_SG_000184",
        "advisor_id": "RM_SG_001",
        "booking_center_code": "Singapore",
    }
    assert response["clientProfile"]["mandate_profile"]["risk_exposure"] == "balanced"
    assert response["keyFigures"]["client_profile"]["objective"] == (
        "Long-term real wealth growth with controlled income and liquidity."
    )
    assert response["keyFigures"]["portfolio_value"] == {
        "total_market_value_reporting_currency": 1_000_000.0,
        "invested_market_value_reporting_currency": 998_800.0,
        "cash_balance_reporting_currency": 50_000.0,
        "cash_weight_pct": 5.0,
    }
    assert response["keyFigures"]["allocation"]["largest_asset_class"] == {
        "name": "Equity",
        "weight_pct": 60.0,
        "market_value_reporting_currency": 600000.0,
        "position_count": 3,
    }
    assert response["keyFigures"]["performance"]["ytd_net_return_pct"] == 4.1
    assert response["keyFigures"]["performance"]["benchmark_comparison_status"] == "available"
    assert response["keyFigures"]["performance"]["ytd_benchmark_return_pct"] == 3.4
    assert response["keyFigures"]["performance"]["ytd_benchmark_relative_return_pct"] == 0.7
    assert response["keyFigures"]["performance"]["contribution_status"] == "present"
    assert response["keyFigures"]["performance"]["largest_positive_contributor"] == {
        "security_id": "EQ-1",
        "position_id": "P1:EQ-1",
        "total_contribution_pct": 3.5,
        "average_weight_pct": 60.0,
        "total_return_pct": 5.8,
    }
    assert response["keyFigures"]["risk"]["ytd_volatility_pct"] == 0.12
    assert response["keyFigures"]["risk"]["ytd_beta"] == 0.82
    assert response["keyFigures"]["risk"]["ytd_tracking_error_pct"] == 0.04
    assert response["keyFigures"]["risk"]["ytd_information_ratio"] == 0.72
    assert response["keyFigures"]["holdings"]["top_holding"]["security_id"] == "EQ-1"
    assert response["keyFigures"]["holdings"]["unrealized_pnl_coverage"] == "present"
    assert response["keyFigures"]["holdings"]["total_unrealized_pnl_reporting_currency"] == 100000.0
    assert response["keyFigures"]["holdings"]["top_five_positive_exposure_pct"] == 100.0
    assert response["keyFigures"]["income_and_activity"]["realized_pnl_status"] == "present"
    assert (
        response["keyFigures"]["income_and_activity"]["total_realized_pnl_reporting_currency"]
        == 1250.0
    )
    assert response["keyFigures"]["transactions"]["realized_pnl_status"] == "present"
    assert response["keyFigures"]["transactions"]["total_realized_pnl_reporting_currency"] == 1250.0
    coverage = {
        group["group_id"]: group["status"] for group in response["reportCoverage"]["figure_groups"]
    }
    assert coverage["client_profile"] == "present"
    assert coverage["portfolio_value"] == "present"
    assert coverage["benchmark_comparison"] == "present"
    assert coverage["position_pnl_and_cost_basis"] == "present"
    assert coverage["performance_contribution"] == "present"
    assert coverage["transaction_realized_gain_loss"] == "present"
    assert coverage["instrument_reference_data"] == "present"
    assert coverage["targets_guidelines_and_suitability"] == "not_sourced"
    assert coverage["tax_lot_and_jurisdiction_tax_treatment"] == "not_sourced"
    assert coverage["advisor_ai_assistance"] == "guarded_ready"
    observation_ids = {item["observation_id"] for item in response["reviewObservations"]}
    assert "suitability_and_mandate_controls_not_sourced" in observation_ids
    assert "benchmark_comparison_not_sourced" not in observation_ids
    assert response["reportStructure"]["presentation_sequence"][0]["section_key"] == (
        "client_and_mandate_context"
    )
    assert response["advisorBriefing"]["required_advisor_checks"][1]["status"] == "not_sourced"
    assert response["aiReadiness"]["status"] == "guarded_ready"
    assert response["aiReadiness"]["blocked_ai_features"][0]["feature_id"] == (
        "trade_recommendation"
    )
    assert response["upstreamCapabilityAudit"]["status"] == "action_required"
    audit_gaps = {
        gap["capability_id"]: gap for gap in response["upstreamCapabilityAudit"]["upstream_gaps"]
    }
    assert audit_gaps["targets_guidelines_suitability"]["owning_service"] == (
        "lotus-advise / lotus-manage"
    )
    assert "tax_lot_jurisdiction_tax_treatment" in audit_gaps
    assert response["upstreamCapabilityAudit"]["report_side_findings"] == []
    assert response["methodology"]["benchmark_code"] == "BMK_GLOBAL_BALANCED_60_40"
    assert response["methodology"]["return_methodology"] == "time_weighted_return"
    requested_periods = [
        period["period"] for period in performance_client.seen_payloads[0]["periods"]
    ]
    assert requested_periods == ["1M", "3M", "YTD", "5Y", "SI"]
    assert performance_client.seen_payloads[0]["include_benchmark"] is True
    assert performance_client.seen_payloads[0]["benchmark"] == {
        "benchmark_id": "BMK_GLOBAL_BALANCED_60_40",
        "input_mode": "stateful",
        "return_source": "calculated",
        "stateful_input": {},
    }
    assert risk_client.seen_payloads == [
        {
            "input_mode": "stateful",
            "stateful_input": {
                "portfolio_id": "P1",
                "as_of_date": "2026-02-24",
                "reporting_currency": "USD",
                "client_id": None,
                "benchmark_id": "BMK_GLOBAL_BALANCED_60_40",
                "net_or_gross": "NET",
                "periods": [
                    {"type": "YTD", "name": "YTD"},
                    {"type": "THREE_YEAR", "name": "THREE_YEAR"},
                ],
                "metrics": [
                    "VOLATILITY",
                    "SHARPE",
                    "DRAWDOWN",
                    "VAR",
                    "BETA",
                    "TRACKING_ERROR",
                    "INFORMATION_RATIO",
                ],
                "options": {
                    "frequency": "DAILY",
                    "var": {
                        "method": "HISTORICAL",
                        "confidence": 0.95,
                        "horizon_days": 1,
                        "include_expected_shortfall": True,
                    },
                },
            },
        }
    ]
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
    assert source_refs["client_profile"]["source_endpoint"] == "/portfolios/P1"
    assert source_refs["performance_review"]["source_service"] == "lotus-performance"
    assert source_refs["risk_review"]["source_service"] == "lotus-risk"
    assert response["evidence"]["trust_metadata"]["completeness_status"] == "complete"
    assert response["evidence"]["trust_metadata"]["data_quality_status"] == "quality_passed"
    assert [section["section_id"] for section in response["client_sections"]] == [
        "client_profile",
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
    assert section_statuses["client_profile"] == "ready"
    assert section_statuses["executive_summary"] == "ready"
    assert section_statuses["asset_allocation"] == "ready"
    assert section_statuses["performance_review"] == "ready"
    assert section_statuses["risk_review"] == "ready"
    advisor_items = _advisor_prompt_items(response)
    assert {item["prompt_id"] for item in advisor_items} == {
        "review_readiness",
        "portfolio_construction_review",
        "performance_discussion",
        "risk_discussion",
    }
    for item in advisor_items:
        assert item["advisor_only"] is True
        assert item["source_section_ids"]
        assert item["source_refs"]
        assert FORBIDDEN_ADVISOR_PROMPT_WORDS.isdisjoint(
            item["prompt"].lower().replace(".", "").replace(",", "").split()
        )
        for route_target in item["route_targets"]:
            assert route_target["portfolio_id"] == "P1"
            assert route_target["as_of_date"] == "2026-02-24"
            assert route_target["mutation_allowed"] is False
    advisor_surfaces = {
        route_target["surface"] for item in advisor_items for route_target in item["route_targets"]
    }
    assert {
        "lotus-workbench",
        "lotus-performance",
        "lotus-risk",
        "lotus-advise",
        "lotus-manage",
    } <= advisor_surfaces
    for client_section in response["client_sections"]:
        assert client_section["section_id"] != "advisor_discussion"
        assert "advisor_only" not in client_section
    assert response["overview"]["total_market_value"] == 1_000_000.0
    assert "YTD" in response["performance"]["summary"]
    assert response["performance"]["benchmark"] == {
        "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
        "requested_benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
        "comparison_status": "available",
        "return_source": "calculated",
        "reason_code": None,
    }
    assert response["performance"]["supportability"] == {"status": "ready", "notes": []}
    assert response["performance"]["summary"]["1M"]["net_annualized_return"] is None
    assert response["performance"]["summary"]["YTD"]["gross_annualized_return"] is None
    assert response["performance"]["summary"]["5Y"]["net_annualized_return"] == 3.9
    assert "YTD" in response["riskAnalytics"]["results"]
    assert response["riskAnalytics"]["source"]["service"] == "lotus-risk"
    assert response["riskAnalytics"]["supportability"] == {"status": "ready", "notes": []}
    assert response["riskAnalytics"]["summary"]["YTD"] == {
        "volatility": 0.12,
        "risk_adjusted_return": 1.05,
        "drawdown": -0.08,
        "value_at_risk": -0.02,
        "beta": 0.82,
        "tracking_error": 0.04,
        "information_ratio": 0.72,
        "benchmark_relative_risk": 0.04,
    }
    assert response["holdings"]["holdingsByAssetClass"]["Equity"][0]["security_id"] == "EQ-1"
    assert (
        response["incomeAndActivity"]["realizedPnlSummary"]["total_realized_pnl_reporting_currency"]
        == 1250.0
    )
    sell_transaction = next(
        item
        for item in response["transactions"]["transactionsByCategory"]["Trading"]
        if item["transaction_id"] == "TXN-SELL-1"
    )
    assert sell_transaction["realized_pnl_reporting_currency"] == 1250.0
    client_sections = {section["section_id"]: section for section in response["client_sections"]}
    assert client_sections["client_profile"]["items"][0] == {
        "item_type": "client_identity",
        "client_id": "CIF_SG_000184",
        "advisor_id": "RM_SG_001",
        "booking_center_code": "Singapore",
        "profile_status": "present",
    }
    assert client_sections["executive_summary"]["items"][0] == {
        "item_type": "measure",
        "metric": "total_market_value",
        "label": "Total market value",
        "value": 1_000_000.0,
        "unit": "money",
        "currency": "",
    }
    assert client_sections["performance_review"]["items"][0]["item_type"] == ("performance_period")
    assert client_sections["performance_review"]["items"][0]["period"] == "1M"
    assert (
        client_sections["performance_review"]["items"][0]["benchmark_comparison_status"]
        == "available"
    )
    assert client_sections["risk_review"]["items"] == [
        {
            "item_type": "risk_period",
            "period": "YTD",
            "volatility": 0.12,
            "risk_adjusted_return": 1.05,
            "drawdown": -0.08,
            "value_at_risk": -0.02,
            "beta": 0.82,
            "tracking_error": 0.04,
            "information_ratio": 0.72,
        }
    ]
    assert client_sections["holdings_appendix"]["items"][0] == {
        "item_type": "holdings_summary",
        "position_count": 2,
    }
    assert client_sections["holdings_appendix"]["items"][1]["item_type"] == "holding"
    assert (
        client_sections["holdings_appendix"]["items"][1]["market_value_reporting_currency"]
        == 600000.0
    )
    assert (
        client_sections["holdings_appendix"]["items"][1]["unrealized_pnl_reporting_currency"]
        == 100000.0
    )
    assert client_sections["holdings_appendix"]["items"][1]["ytd_contribution_pct"] == 3.5


@pytest.mark.asyncio
async def test_review_keeps_available_risk_periods_when_long_benchmark_window_fails():
    risk_client = _RiskClientPeriodFallback()
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=risk_client,
    )

    response = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "reporting_currency": "USD",
            "sections": ["PERFORMANCE", "RISK_ANALYTICS"],
            "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
        },
        None,
    )

    assert len(risk_client.seen_payloads) == 3
    assert response["riskAnalytics"]["supportability"]["status"] == "partial"
    assert response["riskAnalytics"]["summary"]["YTD"] == {
        "volatility": 0.12,
        "risk_adjusted_return": None,
        "drawdown": None,
        "value_at_risk": None,
        "beta": 0.82,
        "tracking_error": 0.04,
        "information_ratio": 0.72,
        "benchmark_relative_risk": 0.04,
    }
    assert "THREE_YEAR" not in response["riskAnalytics"]["summary"]
    assert response["riskAnalytics"]["metadata"]["period_failures"] == [
        {
            "period": "THREE_YEAR",
            "code": "risk_period_upstream_failure",
            "status_code": 424,
            "message": (
                "Benchmark composition window does not cover requested date 2023-04-12 "
                "for benchmark_id=BMK_PB_GLOBAL_BALANCED_60_40."
            ),
        }
    ]
    assert response["riskAnalytics"]["supportability"]["notes"] == [
        {
            "code": "risk_period_upstream_failure",
            "severity": "warning",
            "period": "THREE_YEAR",
            "message": (
                "Benchmark composition window does not cover requested date 2023-04-12 "
                "for benchmark_id=BMK_PB_GLOBAL_BALANCED_60_40."
            ),
        }
    ]
    assert response["keyFigures"]["risk"]["ytd_beta"] == 0.82
    assert response["keyFigures"]["risk"]["three_year_beta"] is None


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
