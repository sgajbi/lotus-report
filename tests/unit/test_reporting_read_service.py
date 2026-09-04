import pytest

from app.application_errors import ReportingNotFoundError, ReportingUpstreamError
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


def _transaction_ledger_metadata(
    *,
    as_of_date: object = "2026-02-24",
    data_quality_status: str = "COMPLETE",
    reason_codes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "product_name": "TransactionLedgerWindow",
        "product_version": "v1",
        "tenant_id": "default",
        "generated_at": "2026-02-24T09:30:00Z",
        "as_of_date": as_of_date,
        "data_quality_status": data_quality_status,
        "reconciliation_status": "RECONCILED",
        "latest_evidence_timestamp": "2026-02-24T09:20:00Z",
        "restatement_version": "fx-restatement:2026-02-24:USD",
        "source_batch_fingerprint": "core-txn-batch:2026-02-24",
        "snapshot_id": "txn-window:P1:2026-02-24",
        "content_hash": "sha256:transaction-window",
        "policy_version": "transaction-ledger-window:v1",
        "correlation_id": "CID-CORE-TXN",
        "reason_codes": reason_codes or ["TRANSACTION_LEDGER_READY"],
    }


def _holdings_as_of_metadata(
    *,
    as_of_date: object = "2026-02-24",
    data_quality_status: str = "COMPLETE",
    reason_codes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "product_name": "HoldingsAsOf",
        "product_version": "v1",
        "tenant_id": "default",
        "generated_at": "2026-02-24T09:30:00Z",
        "as_of_date": as_of_date,
        "data_quality_status": data_quality_status,
        "reconciliation_status": "RECONCILED",
        "latest_evidence_timestamp": "2026-02-24T09:15:00Z",
        "restatement_version": "positions-restatement:2026-02-24:USD",
        "source_batch_fingerprint": "core-holdings-batch:2026-02-24",
        "snapshot_id": "holdings-as-of:P1:2026-02-24",
        "content_hash": "sha256:holdings-as-of",
        "policy_version": "holdings-as-of:v1",
        "correlation_id": "CID-CORE-HOLDINGS",
        "reason_codes": reason_codes or ["HOLDINGS_AS_OF_READY"],
    }


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
            **_transaction_ledger_metadata(as_of_date=params.get("as_of_date")),
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
                    "settlement_date": "2026-02-05",
                    "gross_transaction_amount_reporting_currency": 25000.0,
                    "realized_gain_loss_reporting_currency": 1250.0,
                    "realized_gain_loss_local": 1250.0,
                    "linked_transaction_group_id": "LTG-SELL-1",
                    "source_system": "core-booking",
                    "source_record_id": "SRC-TXN-SELL-1",
                    "costs": [
                        {
                            "fee_type": "BROKERAGE",
                            "amount": 12.5,
                            "currency": "USD",
                        }
                    ],
                    "cashflow": {
                        "cashflow_id": "CF-SELL-1",
                        "cashflow_type": "SALE_PROCEEDS",
                    },
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
            **_holdings_as_of_metadata(as_of_date=params.get("as_of_date")),
            "portfolio_id": portfolio_id,
            "total": 2,
            "positions": [
                {
                    "position_id": "POS-EQ-1",
                    "security_id": "EQ-1",
                    "instrument_name": "Equity 1",
                    "asset_class": "Equity",
                    "isin": "US0000000001",
                    "sector": "Technology",
                    "country_of_risk": "United States",
                    "product_type": "Equity",
                    "liquidity_tier": "L1",
                    "held_since_date": "2025-01-15",
                    "maturity_date": None,
                    "position_state_status": "CURRENT",
                    "position_state_epoch": "7",
                    "latest_evidence_timestamp": "2026-02-24T09:10:00Z",
                    "snapshot_id": "position-snapshot:EQ-1:2026-02-24",
                    "source_system": "core-position-state",
                    "source_record_id": "SRC-POS-EQ-1",
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
                    "position_id": "POS-CASH-1",
                    "security_id": "CASH-1",
                    "instrument_name": "Cash",
                    "asset_class": "Cash",
                    "isin": "CASH-USD",
                    "product_type": "Cash",
                    "liquidity_tier": "L1",
                    "quantity": 1,
                    "position_state_status": "CURRENT",
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
                        "benchmark_id": "BMK_PB_GLOBAL_BALANCED_60_40",
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
                        "benchmark_id": "BMK_PB_GLOBAL_BALANCED_60_40",
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
                "1Y": {
                    "portfolio_twr": {
                        "net": {
                            "summary": {
                                "cumulative_return": {"base": 7.08},
                                "annualized_return": {"base": 7.08},
                            },
                            "breakdowns": {
                                "daily": [
                                    {
                                        "period": "2026-02-24",
                                        "period_end": "2026-02-24",
                                        "period_return": {"base": 1.0},
                                    }
                                ],
                                "monthly": [
                                    {
                                        "period": "2026-01",
                                        "period_start": "2026-01-01",
                                        "period_end": "2026-01-31",
                                        "economics": {
                                            "begin_market_value": 5_000_000.0,
                                            "end_market_value": 5_214_639.0,
                                            "beginning_cash_flow": 5_841_778.0,
                                            "ending_cash_flow": -5_841_749.0,
                                            "net_cash_flow": 29.0,
                                            "flow_adjusted_end_market_value": 5_129_000.0,
                                        },
                                        "period_return": {"base": -1.64},
                                        "cumulative_return": {"base": -1.64},
                                    },
                                    {
                                        "period": "2026-02",
                                        "period_start": "2026-02-01",
                                        "period_end": "2026-02-24",
                                        "economics": {
                                            "begin_market_value": 5_214_639.0,
                                            "end_market_value": 5_296_856.0,
                                            "beginning_cash_flow": 4_722_497.0,
                                            "ending_cash_flow": -4_858_311.0,
                                            "net_cash_flow": -135_814.0,
                                            "flow_adjusted_end_market_value": 5_298_774.0,
                                        },
                                        "period_return": {"base": 1.59},
                                        "cumulative_return": {"base": -0.08},
                                    },
                                ],
                            },
                        }
                    },
                    "benchmark": {
                        "summary": {"cumulative_return": {"base": 6.6}},
                        "benchmark_id": "BMK_PB_GLOBAL_BALANCED_60_40",
                        "return_source": "calculated",
                    },
                    "active": {"net": {"cumulative_return": {"base": 0.48}}},
                    "money_weighted_return": {"start_date": "2025-02-24", "end_date": "2026-02-24"},
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
                                ],
                                "yearly": [
                                    {
                                        "period": "2024",
                                        "period_start": "2024-01-01",
                                        "period_end": "2024-12-31",
                                        "economics": {
                                            "begin_market_value": 4_800_000.0,
                                            "end_market_value": 5_010_000.0,
                                            "beginning_cash_flow": 100_000.0,
                                            "ending_cash_flow": -50_000.0,
                                            "net_cash_flow": 50_000.0,
                                            "flow_adjusted_end_market_value": 4_960_000.0,
                                        },
                                        "period_return": {"base": 3.4},
                                        "cumulative_return": {"base": 8.2},
                                        "annualized_return": {"base": 2.7},
                                    },
                                    {
                                        "period": "2025",
                                        "period_start": "2025-01-01",
                                        "period_end": "2025-12-31",
                                        "economics": {
                                            "begin_market_value": 5_010_000.0,
                                            "end_market_value": 5_296_856.0,
                                            "beginning_cash_flow": 80_000.0,
                                            "ending_cash_flow": -20_000.0,
                                            "net_cash_flow": 60_000.0,
                                            "flow_adjusted_end_market_value": 5_236_856.0,
                                        },
                                        "period_return": {"base": 4.5},
                                        "cumulative_return": {"base": 12.0},
                                        "annualized_return": {"base": 3.9},
                                    },
                                ],
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
                        "benchmark_id": "BMK_PB_GLOBAL_BALANCED_60_40",
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

    async def get_attribution(self, payload):
        self.attribution_requests = getattr(self, "attribution_requests", [])
        self.attribution_requests.append(payload)
        return 200, {
            "results_by_period": {"YTD": {"levels": [], "reconciliation": {}}},
            "model": "brinson_fachler",
            "linking": "carino",
            "benchmark_context": {"benchmark_id": "BMK_RESOLVED"},
        }


class _RollingRiskClientMixin:
    """The shipped /analytics/risk/rolling-metrics answer, minimal but
    production-shaped: one YTD period, one 63-observation window, series
    points with a warm-up null."""

    rolling_payloads: list[dict[str, object]]

    async def rolling_metrics(self, payload: dict[str, object]):
        self.rolling_payloads.append(payload)
        return 200, {
            "source_service": "lotus-risk",
            "input_mode": "stateful",
            "results": {
                "YTD": {
                    "start_date": "2026-01-01",
                    "end_date": "2026-02-24",
                    "benchmark_context": {
                        "requested": True,
                        "available": True,
                        "aligned": True,
                        "reason": "APPLIED",
                    },
                    "quality_flags": [],
                    "error": None,
                    "window_results": [
                        {
                            "window_length": 63,
                            "metric_summaries": {},
                            "metric_series": [
                                {
                                    "date": "2026-02-20",
                                    "metric_values": {"ROLLING_VOLATILITY": None},
                                },
                                {
                                    "date": "2026-02-23",
                                    "metric_values": {"ROLLING_VOLATILITY": 0.137},
                                },
                                {
                                    "date": "2026-02-24",
                                    "metric_values": {"ROLLING_VOLATILITY": 0.141},
                                },
                            ],
                            "metric_series_context": {
                                "requested": True,
                                "included": True,
                                "emitted_point_count": 3,
                                "reason": "INCLUDED",
                            },
                        }
                    ],
                }
            },
            "metadata": {"annualization_basis": 252},
        }


class _AttributionRiskClientMixin:
    """The shipped /analytics/risk/historical-attribution answer, minimal but
    production-shaped: one YTD period, TOTAL_RISK x VOLATILITY x SECTOR with
    the reconciliation triple and two contributors."""

    attribution_payloads: list[dict[str, object]]

    async def historical_attribution(self, payload: dict[str, object]):
        self.attribution_payloads.append(payload)
        return 200, {
            "source_service": "lotus-risk",
            "input_mode": "stateful",
            "results": {
                "YTD": {
                    "start_date": "2026-01-01",
                    "end_date": "2026-02-24",
                    "attribution_sets": [
                        {
                            "attribution_type": "TOTAL_RISK",
                            "metric": "VOLATILITY",
                            "grouping_dimension": "SECTOR",
                            "total_value": 0.1253,
                            "reconciled_sum": 0.1249,
                            "residual": 0.0004,
                            "contributors": [
                                {
                                    "group_key": "SECTOR_TECH",
                                    "group_label": "Technology",
                                    "weight_average": 0.245,
                                    "marginal_contribution": 0.0784,
                                    "component_contribution": 0.0192,
                                    "percent_contribution": 0.1532,
                                }
                            ],
                            "quality_flags": [],
                        }
                    ],
                    "error": None,
                }
            },
            "metadata": {
                "annualization_basis": 252,
                "metric_unit_semantics": {
                    "VOLATILITY": "decimal_ratio",
                    "TRACKING_ERROR": "decimal_ratio",
                },
                "benchmark_context": {"requested": True, "reason": "APPLIED"},
                "stateful_active_risk_gate_reason": "none",
            },
        }


class _RiskClientSuccess(_RollingRiskClientMixin, _AttributionRiskClientMixin):
    def __init__(self):
        self.seen_payloads: list[dict[str, object]] = []
        self.rolling_payloads: list[dict[str, object]] = []
        self.attribution_payloads: list[dict[str, object]] = []

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
        if period.get("name") == "3Y":
            return 424, {
                "error": {
                    "message": (
                        "Benchmark composition window does not cover requested date "
                        "2023-04-12 for benchmark_id=BMK_PB_GLOBAL_BALANCED_60_40."
                    )
                }
            }
        period_name = str(period.get("name") or period.get("type") or "YTD")
        return 200, {
            "results": {
                period_name: {
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
    # The by-type split was computed and discarded before #249; the earnings
    # statement reads it from the snapshot, so the capture must forward it.
    assert set(response["incomeSummary"]["by_income_type"]) <= {"DIVIDEND", "INTEREST"}
    assert response["activitySummary"]["total_inflows"] == 1000.0
    assert response["pnlSummary"]["unrealized_pnl_reporting_currency"] == 100_000.0
    assert response["pnlSummary"]["unrealized_pnl_status"] == "present"
    assert response["pnlSummary"]["realized_pnl_reporting_currency"] == 1_250.0
    assert response["pnlSummary"]["realized_pnl_status"] == "present"
    assert response["pnlSummary"]["total_pnl"] == 101_250.0
    assert response["pnlSummary"]["total_pnl_status"] == "present"
    assert response["pnlSummary"]["source_methodology"] == (
        "sourced_position_unrealized_and_transaction_realized_pnl"
    )
    assert response["pnlSummary"]["supportability"] == {"status": "ready", "notes": []}


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
    assert response["keyFigures"]["performance"]["one_year_net_return_pct"] == 7.08
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
    assert response["keyFigures"]["holdings"]["supportability_status"] == "ready"
    assert response["keyFigures"]["holdings"]["source_data_quality_status"] == "COMPLETE"
    assert response["keyFigures"]["holdings"]["source_product"]["product_name"] == "HoldingsAsOf"
    assert response["keyFigures"]["income_and_activity"]["realized_pnl_status"] == "present"
    assert (
        response["keyFigures"]["income_and_activity"]["total_realized_pnl_reporting_currency"]
        == 1250.0
    )
    assert response["keyFigures"]["transactions"]["realized_pnl_status"] == "present"
    assert response["keyFigures"]["transactions"]["total_realized_pnl_reporting_currency"] == 1250.0
    assert response["keyFigures"]["transactions"]["source_data_quality_status"] == "COMPLETE"
    assert response["keyFigures"]["transactions"]["source_product"]["product_name"] == (
        "TransactionLedgerWindow"
    )
    assert response["transactions"]["sourceProduct"]["latest_evidence_timestamp"] == (
        "2026-02-24T09:20:00Z"
    )
    assert response["transactions"]["supportability"] == {"status": "ready", "notes": []}
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
    assert response["methodology"]["benchmark_code"] == "BMK_PB_GLOBAL_BALANCED_60_40"
    assert response["methodology"]["return_methodology"] == "time_weighted_return"
    assert len(response["performance"]["monthly_history"]) == 2
    assert response["performance"]["monthly_history"][1] == {
        "period": "2026-02",
        "period_start": "2026-02-01",
        "period_end": "2026-02-24",
        "begin_market_value": 5214639.0,
        "end_market_value": 5296856.0,
        "inflows": 4722497.0,
        "outflows": -4858311.0,
        "net_cash_flow": -135814.0,
        "performance_value": 84135.0,
        "cumulative_performance_value": 213135.0,
        "twr_pct": 1.59,
        "cumulative_twr_pct": -0.08,
        "annualized_twr_pct": None,
    }
    assert len(response["performance"]["annual_history"]) == 2
    assert response["performance"]["annual_history"][1]["period"] == "2025"
    assert response["performance"]["annual_history"][1]["annualized_twr_pct"] == 3.9
    requested_periods = [
        period["period"] for period in performance_client.seen_payloads[0]["periods"]
    ]
    assert requested_periods == ["1M", "3M", "YTD", "1Y", "5Y", "SI"]
    requested_frequencies = {
        period["period"]: period["frequencies"]
        for period in performance_client.seen_payloads[0]["periods"]
    }
    assert requested_frequencies["1Y"] == ["daily", "monthly"]
    assert requested_frequencies["5Y"] == ["daily", "yearly"]
    assert performance_client.seen_payloads[0]["include_benchmark"] is True
    assert performance_client.seen_payloads[0]["benchmark"] == {
        "benchmark_id": "BMK_PB_GLOBAL_BALANCED_60_40",
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
                "benchmark_id": "BMK_PB_GLOBAL_BALANCED_60_40",
                "net_or_gross": "NET",
                "periods": [
                    {"type": "YTD", "name": "YTD"},
                    {"type": "1Y", "name": "1Y"},
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
    assert source_refs["holdings_appendix"]["source_product"]["product_version"] == "v1"
    assert source_refs["performance_review"]["source_service"] == "lotus-performance"
    assert source_refs["risk_review"]["source_service"] == "lotus-risk"
    assert source_refs["transactions_appendix"]["source_product"]["product_version"] == "v1"
    assert response["evidence"]["trust_metadata"]["completeness_status"] == "complete"
    assert response["evidence"]["trust_metadata"]["data_quality_status"] == "quality_passed"
    assert [section["section_id"] for section in response["client_sections"]] == [
        "client_profile",
        "executive_summary",
        "advisor_commentary",
        "asset_allocation",
        "performance_review",
        "performance_attribution",
        "risk_review",
        "risk_attribution",
        "income_cash_activity",
        "holdings_appendix",
        "transactions_appendix",
    ]
    commentary_section = next(
        section
        for section in response["client_sections"]
        if section["section_id"] == "advisor_commentary"
    )
    assert commentary_section["status"] == "omitted_by_request"
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
        "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
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
    assert sell_transaction["transaction_date"] == "2026-02-03"
    assert sell_transaction["settlement_date"] == "2026-02-05"
    assert sell_transaction["linked_transaction_group_id"] == "LTG-SELL-1"
    assert sell_transaction["linked_costs"] == [
        {"fee_type": "BROKERAGE", "amount": 12.5, "currency": "USD"}
    ]
    assert sell_transaction["linked_cashflow"] == {
        "cashflow_id": "CF-SELL-1",
        "cashflow_type": "SALE_PROCEEDS",
    }
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
    assert client_sections["holdings_appendix"]["items"][0]["item_type"] == "holdings_summary"
    assert client_sections["holdings_appendix"]["items"][0]["position_count"] == 2
    assert (
        client_sections["holdings_appendix"]["items"][0]["source_product"]["product_name"]
        == "HoldingsAsOf"
    )
    assert client_sections["holdings_appendix"]["items"][1]["item_type"] == "holding"
    assert (
        client_sections["holdings_appendix"]["items"][1]["market_value_reporting_currency"]
        == 600000.0
    )
    assert client_sections["holdings_appendix"]["items"][1]["position_state_status"] == "CURRENT"
    assert client_sections["holdings_appendix"]["items"][1]["row_snapshot_id"] == (
        "position-snapshot:EQ-1:2026-02-24"
    )
    assert client_sections["holdings_appendix"]["items"][1]["source_record_id"] == "SRC-POS-EQ-1"
    assert (
        client_sections["holdings_appendix"]["items"][1]["unrealized_pnl_reporting_currency"]
        == 100000.0
    )
    assert client_sections["holdings_appendix"]["items"][1]["ytd_contribution_pct"] == 3.5


@pytest.mark.asyncio
async def test_review_keeps_available_risk_periods_when_combined_risk_request_fails():
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
    assert response["riskAnalytics"]["supportability"]["status"] == "ready"
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
    assert response["riskAnalytics"]["summary"]["1Y"]["beta"] == 0.82
    assert "period_failures" not in response["riskAnalytics"]["metadata"]
    assert response["riskAnalytics"]["supportability"]["notes"] == []
    assert response["keyFigures"]["risk"]["ytd_beta"] == 0.82
    assert response["keyFigures"]["risk"]["one_year_beta"] == 0.82


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
    with pytest.raises(ReportingNotFoundError):
        await service.get_portfolio_summary("P404", {"as_of_date": "2026-02-24"}, None)


@pytest.mark.asyncio
async def test_core_query_failure_maps_to_502():
    service = ReportingReadService(
        core_query_client=_CoreQueryClientFailure(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    with pytest.raises(ReportingUpstreamError):
        await service.get_portfolio_review("P1", {"as_of_date": "2026-02-24"}, None)


@pytest.mark.asyncio
async def test_ordering_attribution_composes_the_section_and_honours_defaulting():
    """The wiring, proven through the real read service rather than the
    capture module alone: ordering PERFORMANCE_ATTRIBUTION composes
    response["attribution"], and an order without a benchmark code reaches
    the source as an OMISSION (the catalogue's defaulting policy), never "".
    """

    performance_client = _PerformanceClientSuccess()
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=performance_client,
        risk_client=_RiskClientSuccess(),
    )

    response = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "reporting_currency": "USD",
            "sections": ["OVERVIEW", "PERFORMANCE_ATTRIBUTION"],
        },
        None,
    )

    assert response["attribution"]["status"] == "present"
    submitted = performance_client.attribution_requests[0]
    assert "benchmark_id" not in submitted["stateful_input"]

    pending_client = _PerformanceClientSuccess()

    async def _accepted(payload):
        return 202, {"calculation_id": "calc-9", "result_path": "/p"}

    pending_client.get_attribution = _accepted
    pending_service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=pending_client,
        risk_client=_RiskClientSuccess(),
    )
    pending_response = await pending_service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "reporting_currency": "USD",
            "sections": ["OVERVIEW", "PERFORMANCE_ATTRIBUTION"],
        },
        None,
    )
    # A still-computing section must not report ready on the JSON surface: the
    # data is not there, and "ready" would be the presence-implies-health
    # inference this whole vocabulary exists to remove.
    pending_section = next(
        section
        for section in pending_response["client_sections"]
        if section["section_id"] == "performance_attribution"
    )
    assert pending_section["status"] == "pending"
    assert pending_section["reason_code"] == "attribution_accepted_not_complete"

    without_section = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "reporting_currency": "USD",
            "sections": ["OVERVIEW"],
        },
        None,
    )
    assert "attribution" not in without_section


@pytest.mark.asyncio
async def test_risk_trend_rides_the_ordered_risk_section_verbatim():
    """#255 capture: one rolling-metrics call under the RISK_ANALYTICS
    section, the source's results forwarded verbatim into riskTrend, the
    request stating window/metrics/frequency. Benchmark-dependent metrics
    are requested only because the report states a benchmark."""

    risk_client = _RiskClientSuccess()
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=risk_client,
    )
    response = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "sections": ["RISK_ANALYTICS"],
            "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
        },
        "CID-1",
    )

    trend = response["riskTrend"]
    assert trend["source"]["endpoint"] == "/analytics/risk/rolling-metrics"
    assert trend["supportability"]["status"] == "ready"
    assert trend["request"]["window_observations"] == 63
    assert trend["request"]["metrics"] == [
        "ROLLING_VOLATILITY",
        "ROLLING_BETA",
        "ROLLING_TRACKING_ERROR",
    ]
    # Forwarded verbatim: the warm-up null is still there, untouched.
    points = trend["results"]["YTD"]["window_results"][0]["metric_series"]
    assert points[0]["metric_values"]["ROLLING_VOLATILITY"] is None
    assert len(risk_client.rolling_payloads) == 1
    rolling_options = risk_client.rolling_payloads[0]["stateful_input"]["rolling_options"]
    assert rolling_options["include_time_series"] is True
    assert rolling_options["window_lengths"] == [63]


@pytest.mark.asyncio
async def test_risk_trend_without_a_benchmark_requests_volatility_only():
    risk_client = _RiskClientSuccess()
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=risk_client,
    )
    await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["RISK_ANALYTICS"]},
        "CID-1",
    )

    rolling_options = risk_client.rolling_payloads[0]["stateful_input"]["rolling_options"]
    assert rolling_options["metrics"] == ["ROLLING_VOLATILITY"]


@pytest.mark.asyncio
async def test_risk_trend_upstream_failure_is_a_stated_unavailability():
    class _RollingDownRiskClient(_RiskClientSuccess):
        async def rolling_metrics(self, payload: dict[str, object]):
            return 503, {"detail": "rolling unavailable"}

    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_RollingDownRiskClient(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["RISK_ANALYTICS"]},
        "CID-1",
    )

    trend = response["riskTrend"]
    assert trend["supportability"]["status"] == "unavailable"
    assert trend["supportability"]["notes"][0]["code"] == "risk_trend_upstream_failure"
    assert trend["results"] == {}
    # The point-in-time risk block is unaffected by the trend call's fate.
    assert response["riskAnalytics"]["supportability"]["status"] != "unavailable"


@pytest.mark.asyncio
async def test_risk_attribution_is_captured_only_when_ordered_explicitly():
    """#254 capture: the section never rides RISK_ANALYTICS silently - the
    evidence-gated default stays off, and ordering RISK_ATTRIBUTION makes
    exactly one attribution call whose answer is forwarded verbatim."""

    risk_client = _RiskClientSuccess()
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=risk_client,
    )
    without = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["RISK_ANALYTICS"]},
        "CID-1",
    )
    assert "riskAttribution" not in without
    assert risk_client.attribution_payloads == []

    response = await service.get_portfolio_review(
        "P1",
        {
            "as_of_date": "2026-02-24",
            "sections": ["RISK_ATTRIBUTION"],
            "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
        },
        "CID-1",
    )

    attribution = response["riskAttribution"]
    assert attribution["source"]["endpoint"] == "/analytics/risk/historical-attribution"
    assert attribution["supportability"]["status"] == "ready"
    assert attribution["request"]["attribution_types"] == ["TOTAL_RISK", "ACTIVE_RISK"]
    assert attribution["request"]["metrics"] == ["VOLATILITY", "TRACKING_ERROR"]
    assert attribution["request"]["grouping_dimension"] == "SECTOR"
    # Forwarded verbatim, reconciliation triple intact.
    stated = attribution["results"]["YTD"]["attribution_sets"][0]
    assert (stated["total_value"], stated["reconciled_sum"], stated["residual"]) == (
        0.1253,
        0.1249,
        0.0004,
    )
    assert attribution["metadata"]["metric_unit_semantics"]["VOLATILITY"] == "decimal_ratio"
    assert len(risk_client.attribution_payloads) == 1
    options = risk_client.attribution_payloads[0]["stateful_input"]["attribution_options"]
    assert options["grouping_dimensions"] == ["SECTOR"]


@pytest.mark.asyncio
async def test_risk_attribution_without_a_benchmark_requests_total_risk_only():
    risk_client = _RiskClientSuccess()
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=risk_client,
    )
    await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["RISK_ATTRIBUTION"]},
        "CID-1",
    )

    options = risk_client.attribution_payloads[0]["stateful_input"]["attribution_options"]
    assert options["attribution_types"] == ["TOTAL_RISK"]
    assert options["metrics"] == ["VOLATILITY"]


@pytest.mark.asyncio
async def test_risk_attribution_upstream_failure_is_a_stated_refusal():
    class _FailingAttribution(_RiskClientSuccess):
        async def historical_attribution(self, payload):
            return 503, {"detail": "unavailable"}

    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_FailingAttribution(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["RISK_ATTRIBUTION"]},
        "CID-1",
    )

    attribution = response["riskAttribution"]
    assert attribution["supportability"]["status"] == "unavailable"
    assert attribution["supportability"]["notes"][0]["code"] == "risk_attribution_upstream_failure"
    assert attribution["results"] == {}


@pytest.mark.asyncio
async def test_risk_attribution_transport_failure_is_the_same_stated_refusal():
    class _RaisingAttribution(_RiskClientSuccess):
        async def historical_attribution(self, payload):
            raise RuntimeError("connection reset")

    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_RaisingAttribution(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["RISK_ATTRIBUTION"]},
        "CID-1",
    )

    attribution = response["riskAttribution"]
    assert attribution["supportability"]["status"] == "unavailable"
    assert attribution["supportability"]["notes"][0]["code"] == "risk_attribution_upstream_failure"


@pytest.mark.asyncio
async def test_evidence_trust_claims_state_only_what_is_proven():
    """#283: the pack's tenant is the ADMITTED tenant (never a hardcoded
    default), an unattributed caller yields no tenant claim at all, the
    reconciliation status is unknown until a policy proves it, and the
    synchronous flow is explicitly ephemeral."""

    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )

    admitted = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["OVERVIEW"]},
        "CID-1",
        admitted_tenant_id="tenant-sg",
    )
    trust = admitted["evidence"]["trust_metadata"]
    assert admitted["evidence"]["evidence_posture"] == "ephemeral_composition"
    assert trust["tenant_id"] == "tenant-sg"
    assert trust["tenant_admission"] == "caller_admitted"
    assert trust["reconciliation_status"] == "unknown"
    assert trust["reconciliation_reason_code"] == "no_reconciliation_policy_established"

    unattributed = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["OVERVIEW"]},
        "CID-2",
    )
    trust = unattributed["evidence"]["trust_metadata"]
    assert "tenant_id" not in trust
    assert trust["tenant_admission"] == "unattributed_caller"
