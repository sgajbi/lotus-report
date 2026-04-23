from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AggregationScope(BaseModel):
    portfolio_id: str
    as_of_date: date


class AggregationRow(BaseModel):
    bucket: str
    metric: str
    value: float


class PortfolioAggregationResponse(BaseModel):
    source_service: str = "lotus-report"
    scope: AggregationScope
    generated_at: datetime
    rows: list[AggregationRow]


class IntegrationCapabilitiesResponse(BaseModel):
    source_service: str = "lotus-report"
    contract_version: str
    policy_version: str = "ras-default-v1"
    features: list[dict[str, str | bool]]
    workflows: list[dict[str, str | bool]]
    supported_input_modes: list[str]


PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE: dict[str, Any] = {
    "as_of_date": "2026-04-22",
    "sections": [
        "CLIENT_PROFILE",
        "OVERVIEW",
        "ALLOCATION",
        "PERFORMANCE",
        "RISK_ANALYTICS",
        "INCOME_AND_ACTIVITY",
        "HOLDINGS",
        "TRANSACTIONS",
    ],
    "reporting_currency": "USD",
    "allocation_dimensions": ["asset_class", "currency", "sector", "region"],
    "look_through_mode": "direct_only",
    "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
}


PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE: dict[str, Any] = {
    "contract_version": "v1",
    "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
    "as_of_date": "2026-04-22",
    "generated_at": "2026-04-22T09:00:00Z",
    "review_period": {
        "as_of_date": "2026-04-22",
        "periods": ["YTD", "1Y"],
        "performance_start_date": "2026-01-01",
    },
    "reporting_currency": "USD",
    "audience": {
        "client_ready": True,
        "advisor_only_sections": ["advisor_discussion"],
        "client_distribution_allowed": True,
    },
    "readiness": {"status": "partial", "reason": "Some upstream suitability data is not sourced."},
    "methodology": {
        "performance_basis": "NET_AND_GROSS_WHERE_AVAILABLE",
        "return_methodology": "time_weighted_return",
        "risk_basis": "ex_post_volatility_and_drawdown",
        "valuation_basis": "reporting_currency_market_value",
    },
    "evidence": {
        "product_id": "lotus-report:ClientReportEvidencePack:v1",
        "lineage_bundle_id": (
            "lineage:lotus-report:portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22"
        ),
        "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
        "calculation_policy": "source_backed_no_invented_figures",
    },
    "key_figures": {
        "portfolio": {
            "total_market_value": 1321400.0,
            "total_cash": 73500.0,
            "net_invested_amount": 1282430.0,
            "reporting_currency": "USD",
            "position_count": 8,
        },
        "allocation": {
            "largest_asset_class": "EQUITY",
            "largest_asset_class_weight_pct": 48.2,
            "cash_weight_pct": 5.56,
            "top_issuer_concentration_pct": 7.9,
        },
        "performance": {
            "ytd_net_return_pct": 4.18,
            "one_year_net_return_pct": 7.42,
            "ytd_benchmark_relative_return_pct": 0.64,
            "top_contributor_ytd_pct": 0.83,
            "bottom_contributor_ytd_pct": -0.21,
        },
        "risk": {
            "portfolio_volatility_pct": 9.8,
            "max_drawdown_pct": -5.7,
            "value_at_risk_95_pct": -2.4,
            "risk_level": "balanced",
        },
        "income_and_activity": {
            "income_ytd": 18420.0,
            "fees_ytd": 2410.0,
            "net_cash_flow_ytd": -12500.0,
            "total_realized_pnl_reporting_currency": 1250.0,
            "transaction_count": 19,
        },
        "holdings": {
            "total_unrealized_pnl": 38970.67,
            "total_unrealized_pnl_pct": 2.95,
            "largest_position_weight_pct": 7.9,
        },
        "transactions": {
            "last_activity_date": "2026-04-19",
            "purchases_ytd": 95000.0,
            "sales_ytd": 72000.0,
            "total_realized_pnl_reporting_currency": 1250.0,
        },
    },
    "report_coverage": {
        "client_profile": {"status": "present", "required": True},
        "portfolio_snapshot": {"status": "present", "required": True},
        "allocation": {"status": "present", "required": True},
        "performance_and_contribution": {"status": "present", "required": True},
        "risk_analytics": {"status": "present", "required": True},
        "income_and_activity": {"status": "present", "required": True},
        "transaction_realized_gain_loss": {"status": "present", "required": False},
        "position_pnl_and_cost_basis": {"status": "present", "required": True},
        "tax_lot_and_jurisdiction_tax_treatment": {
            "status": "not_sourced",
            "required": False,
        },
        "targets_guidelines_and_suitability": {"status": "not_sourced", "required": True},
    },
    "upstream_capability_audit": {
        "status": "action_required",
        "source_backed_capabilities": [
            {
                "capability_id": "holdings_pnl_cost_basis",
                "source_service": "lotus-core",
                "status": "present",
            },
            {
                "capability_id": "performance_contribution",
                "source_service": "lotus-performance",
                "status": "present",
            },
            {
                "capability_id": "transaction_realized_gain_loss",
                "source_service": "lotus-core",
                "status": "present",
            },
        ],
        "upstream_gaps": [
            {
                "capability_id": "targets_guidelines_suitability",
                "owning_service": "lotus-advise / lotus-manage",
                "status": "not_sourced",
            },
            {
                "capability_id": "tax_lot_jurisdiction_tax_treatment",
                "owning_service": "lotus-core / tax domain",
                "status": "not_sourced",
            },
        ],
        "report_side_findings": [],
    },
    "review_observations": [
        {
            "severity": "attention",
            "observation_id": "cash_weight",
            "message": "Cash weight is above the client minimum liquidity buffer.",
            "source_section_ids": ["asset_allocation"],
        }
    ],
    "client_profile": {
        "status": "present",
        "identity": {
            "client_id": "CIF_SG_000184",
            "client_display_name": "Canonical Global Balanced Client",
            "advisor_id": "RM_SG_001",
            "booking_center_code": "SG",
            "relationship_segment": "private_banking",
        },
        "portfolio_profile": {
            "portfolio_name": "Global Balanced Mandate",
            "base_currency": "USD",
            "inception_date": "2021-07-01",
        },
        "mandate_profile": {
            "portfolio_type": "discretionary",
            "objective": "Long-term real wealth growth with controlled income and liquidity.",
            "risk_exposure": "balanced",
            "investment_time_horizon": "7Y_PLUS",
            "is_leverage_allowed": False,
            "cost_basis_method": "FIFO",
        },
        "missing_fields": [],
    },
    "report_structure": {
        "sequence": [
            {
                "order": 1,
                "title": "Client, Mandate, And Meeting Context",
                "section_ids": ["client_profile"],
            },
            {
                "order": 2,
                "title": "Portfolio Review",
                "section_ids": [
                    "executive_summary",
                    "asset_allocation",
                    "performance_review",
                    "risk_review",
                ],
            },
            {
                "order": 3,
                "title": "Appendices",
                "section_ids": ["holdings_appendix", "transactions_appendix"],
            },
        ]
    },
    "advisor_briefing": {
        "status": "ready",
        "briefings": [
            {
                "briefing_id": "meeting_focus",
                "title": "Meeting focus",
                "talking_points": [
                    (
                        "Discuss YTD return drivers, current risk posture, liquidity, "
                        "and cash activity."
                    )
                ],
                "required_checks": [
                    "Confirm suitability and guideline data outside this payload before advice."
                ],
            }
        ],
    },
    "ai_readiness": {
        "status": "guarded_ready",
        "mode": "grounded_assistance_metadata_only",
        "allowed_features": ["summarization", "question_answering_with_citations"],
        "blocked_features": [
            "trade_recommendation",
            "suitability_determination",
            "client_profile_inference",
        ],
    },
    "disclosures": [
        {
            "disclosure_id": "source_backed_reporting",
            "text": (
                "Figures are sourced from Lotus domain services and unsupported data is marked."
            ),
        }
    ],
    "client_sections": [
        {
            "section_id": "client_profile",
            "title": "Client And Mandate Profile",
            "status": "ready",
            "items": [{"client_id": "CIF_SG_000184", "risk_exposure": "balanced"}],
        },
        {
            "section_id": "executive_summary",
            "title": "Executive Review Summary",
            "status": "ready",
            "items": [{"total_market_value": 1321400.0, "ytd_net_return_pct": 4.18}],
        },
        {
            "section_id": "holdings_appendix",
            "title": "Holdings Appendix",
            "status": "ready",
            "items": [{"position_id": "POS_EQ_US_001", "unrealized_pnl": 12450.0}],
        },
    ],
    "advisor_sections": [
        {
            "section_id": "advisor_discussion",
            "title": "Advisor Discussion And Follow-Up",
            "status": "ready",
            "items": [
                {
                    "prompt_id": "review_readiness",
                    "advisor_only": True,
                    "prompt": "Confirm suitability, guideline, and target-allocation context.",
                    "source_section_ids": ["client_profile", "risk_review"],
                    "route_targets": [
                        {
                            "target_id": "workbench_review",
                            "surface": "lotus-workbench",
                            "route_key": "portfolio_review",
                            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                            "as_of_date": "2026-04-22",
                            "mutation_allowed": False,
                        }
                    ],
                }
            ],
        }
    ],
    "overview": {
        "total_market_value": 1321400.0,
        "total_cash": 73500.0,
        "currency": "USD",
    },
    "allocation": {
        "by_asset_class": [
            {"asset_class": "EQUITY", "market_value": 636914.8, "weight_pct": 48.2},
            {"asset_class": "FIXED_INCOME", "market_value": 478746.8, "weight_pct": 36.23},
            {"asset_class": "CASH", "market_value": 73500.0, "weight_pct": 5.56},
        ],
        "by_currency": [{"currency": "USD", "market_value": 1023300.0, "weight_pct": 77.44}],
    },
    "performance": {
        "summary": {
            "YTD": {"net_cumulative_return": 4.18, "benchmark_relative_return": 0.64},
            "1Y": {"net_cumulative_return": 7.42, "benchmark_relative_return": 0.91},
        },
        "contribution": {
            "by_asset_class": [
                {"asset_class": "EQUITY", "contribution_pct": 2.37},
                {"asset_class": "FIXED_INCOME", "contribution_pct": 1.11},
            ],
            "by_position": [{"position_id": "POS_EQ_US_001", "contribution_pct": 0.83}],
        },
    },
    "risk_analytics": {
        "summary": {
            "volatility_pct": 9.8,
            "max_drawdown_pct": -5.7,
            "value_at_risk_95_pct": -2.4,
        },
        "exposures": [{"risk_factor": "equity_beta", "value": 0.62}],
    },
    "income_and_activity": {
        "summary": {"income_ytd": 18420.0, "fees_ytd": 2410.0, "net_cash_flow_ytd": -12500.0},
        "realized_pnl_summary": {
            "status": "present",
            "total_realized_pnl_reporting_currency": 1250.0,
            "transaction_count": 1,
        },
        "cash_flow_breakdown": [
            {"category": "DIVIDENDS", "amount": 9600.0},
            {"category": "COUPONS", "amount": 8820.0},
        ],
    },
    "holdings": {
        "position_count": 8,
        "holdings_by_asset_class": {
            "EQUITY": [
                {
                    "position_id": "POS_EQ_US_001",
                    "instrument_name": "Global Quality Equity Fund",
                    "quantity": 2200.0,
                    "market_value": 104360.0,
                    "weight_pct": 7.9,
                    "cost_basis": 91910.0,
                    "unrealized_pnl": 12450.0,
                    "unrealized_pnl_pct": 13.55,
                    "contribution_ytd_pct": 0.83,
                }
            ]
        },
    },
    "transactions": {
        "transaction_count": 19,
        "transactions_by_category": {
            "INCOME": [
                {
                    "transaction_id": "TXN_20260419_DIV_001",
                    "trade_date": "2026-04-19",
                    "category": "DIVIDEND",
                    "amount": 1250.0,
                    "currency": "USD",
                    "instrument_name": "Global Quality Equity Fund",
                },
                {
                    "transaction_id": "TXN_20260418_SELL_001",
                    "trade_date": "2026-04-18",
                    "category": "SELL",
                    "amount": 25000.0,
                    "realized_pnl_reporting_currency": 1250.0,
                    "currency": "USD",
                    "instrument_name": "Global Quality Equity Fund",
                },
            ]
        },
    },
}


class PortfolioReviewReportRequest(BaseModel):
    as_of_date: date = Field(
        ...,
        description="Report as-of date for portfolio holdings, transactions, and analytics.",
        examples=["2026-04-22"],
    )
    sections: list[str] | None = Field(
        default=None,
        description=(
            "Optional requested report sections. Supported values include OVERVIEW, ALLOCATION, "
            "CLIENT_PROFILE, PERFORMANCE, RISK_ANALYTICS, INCOME_AND_ACTIVITY, HOLDINGS, "
            "and TRANSACTIONS."
        ),
        examples=[
            [
                "OVERVIEW",
                "ALLOCATION",
                "PERFORMANCE",
                "RISK_ANALYTICS",
                "INCOME_AND_ACTIVITY",
                "HOLDINGS",
                "TRANSACTIONS",
            ]
        ],
    )
    reporting_currency: str | None = Field(
        default=None,
        description=(
            "Preferred reporting currency for the review. When omitted, lotus-report uses the "
            "currency available from upstream portfolio context."
        ),
        examples=["USD"],
    )
    allocation_dimensions: list[str] | None = Field(
        default=None,
        description=(
            "Optional allocation dimensions requested by the caller, such as asset_class, "
            "currency, sector, region, country, product_type, rating, or issuer where "
            "supported by upstream data."
        ),
        examples=[["asset_class", "currency"]],
    )
    look_through_mode: str | None = Field(
        default=None,
        description=(
            "Optional look-through handling requested by the caller. Current reporting behavior "
            "passes this as request context and does not invent look-through holdings."
        ),
        examples=["direct_only"],
    )
    benchmark_code: str | None = Field(
        default=None,
        description=(
            "Benchmark identifier used for benchmark-relative performance and risk supportability "
            "when sourced benchmark return series are available."
        ),
        examples=["BMK_GLOBAL_BALANCED_60_40"],
    )

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE,
            "examples": [
                PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE,
                {
                    "as_of_date": "2026-04-22",
                    "sections": ["OVERVIEW", "HOLDINGS"],
                    "reporting_currency": "SGD",
                    "benchmark_code": "BMK_APAC_BALANCED",
                },
            ],
        },
    }


class PortfolioReviewReadiness(BaseModel):
    status: Literal["ready", "partial", "unavailable"] = Field(
        description="Overall report readiness across requested client-ready sections."
    )
    reason: str | None = Field(
        default=None,
        description="Human-readable readiness reason when the report is partial or unavailable.",
    )


class PortfolioReviewSection(BaseModel):
    section_id: str = Field(description="Stable machine-readable section identifier.")
    title: str = Field(description="Display title for the report section.")
    status: Literal["ready", "partial", "unavailable", "omitted_by_request", "not_applicable"] = (
        Field(description="Readiness state for this section in the selected request.")
    )
    reason_code: str | None = Field(
        default=None,
        description="Stable reason code explaining non-ready or non-applicable section state.",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable section status message for operators and advisor surfaces.",
    )
    items: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Normalized machine-readable section rows, prompts, or measures.",
    )


class PortfolioReviewReportResponse(BaseModel):
    contract_version: str = Field(
        default="v1",
        description="Version of the lotus-report portfolio review response contract.",
    )
    report_id: str = Field(
        description="Stable report identifier for this portfolio and as-of date."
    )
    portfolio_id: str = Field(description="Canonical portfolio identifier.")
    as_of_date: date = Field(description="Report as-of date used for the review.")
    generated_at: datetime = Field(
        description="UTC timestamp when lotus-report generated the payload."
    )
    review_period: dict[str, Any] | None = Field(
        default=None,
        validation_alias="reviewPeriod",
        description="Review-period context such as as-of date and period labels.",
    )
    reporting_currency: str | None = Field(
        default=None,
        validation_alias="reportingCurrency",
        description="Reporting currency used for portfolio-level monetary figures.",
    )
    audience: dict[str, Any] = Field(
        default_factory=dict,
        description="Audience posture describing client-ready and advisor-only content separation.",
    )
    readiness: PortfolioReviewReadiness
    methodology: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Methodology and request-context metadata for performance, risk, and reporting."
        ),
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Source refs, lineage bundle, trust metadata, and domain-product evidence.",
    )
    key_figures: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="keyFigures",
        description=(
            "Normalized front-office figures for portfolio value, allocation, performance, "
            "contribution, risk, income/activity, holdings, P&L, transactions, and profile state."
        ),
    )
    report_coverage: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="reportCoverage",
        description=(
            "Coverage map showing which gold-standard report families are present, partial, "
            "unavailable, or not sourced."
        ),
    )
    upstream_capability_audit: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="upstreamCapabilityAudit",
        description=(
            "Machine-readable audit separating source-backed report capabilities from gaps that "
            "require upstream domain features or fixes before the report can certify them."
        ),
    )
    review_observations: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias="reviewObservations",
        description="Advisor attention points derived from sourced figures and explicit gaps.",
    )
    client_profile: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="clientProfile",
        description=(
            "Source-backed client, advisor, booking-center, portfolio, and mandate context from "
            "lotus-core where available. Missing profile fields are explicit gaps."
        ),
    )
    report_structure: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="reportStructure",
        description=(
            "Recommended meeting-pack sequence for UI, document, or presentation consumers."
        ),
    )
    advisor_briefing: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="advisorBriefing",
        description="Deterministic advisor-only talking points and required pre-meeting checks.",
    )
    ai_readiness: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="aiReadiness",
        description=(
            "Guarded AI-assistance metadata. This endpoint does not generate advice, trade "
            "recommendations, suitability determinations, or inferred client profiles."
        ),
    )
    disclosures: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Report disclosures and limitations for client/advisor review use.",
    )
    client_sections: list[PortfolioReviewSection] = Field(
        default_factory=list,
        description="Ordered client-ready report sections with explicit readiness states.",
    )
    advisor_sections: list[PortfolioReviewSection] = Field(
        default_factory=list,
        description="Advisor-only review prompts and route targets. Not client report content.",
    )
    overview: dict[str, Any] | None = Field(
        default=None,
        description="Portfolio snapshot and headline values when the overview section is sourced.",
    )
    allocation: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Allocation breakdowns when requested and sourced from upstream portfolio data."
        ),
    )
    performance: dict[str, Any] | None = Field(
        default=None,
        description="Performance periods, contribution, and supportability when sourced.",
    )
    risk_analytics: dict[str, Any] | None = Field(
        default=None,
        validation_alias="riskAnalytics",
        description="Risk analytics, metric summary, and benchmark supportability when sourced.",
    )
    income_and_activity: dict[str, Any] | None = Field(
        default=None,
        validation_alias="incomeAndActivity",
        description="Income, fee, cash-flow, and activity summary when requested and sourced.",
    )
    holdings: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Position-level holdings, reference data, cost basis, unrealized P&L, and contribution."
        ),
    )
    transactions: dict[str, Any] | None = Field(
        default=None,
        description="Categorized transaction review rows and activity summaries.",
    )

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE,
            "examples": [PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE],
        },
    }
