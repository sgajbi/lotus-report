from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AggregationScope(BaseModel):
    portfolio_id: str = Field(..., alias="portfolioId")
    as_of_date: date = Field(..., alias="asOfDate")

    model_config = {"populate_by_name": True}


class AggregationRow(BaseModel):
    bucket: str
    metric: str
    value: float


class PortfolioAggregationResponse(BaseModel):
    source_service: str = Field("lotus-report", alias="sourceService")
    scope: AggregationScope
    generated_at: datetime = Field(..., alias="generatedAt")
    rows: list[AggregationRow]

    model_config = {"populate_by_name": True}


class ReportRequest(BaseModel):
    portfolio_id: str = Field(..., alias="portfolioId")
    as_of_date: date = Field(..., alias="asOfDate")
    report_type: Literal["PORTFOLIO_SNAPSHOT", "PERFORMANCE_SUMMARY"] = Field(
        ..., alias="reportType"
    )
    output_format: Literal["JSON", "PDF"] = Field("JSON", alias="outputFormat")

    model_config = {"populate_by_name": True}


class ReportResponse(BaseModel):
    report_id: str = Field(..., alias="reportId")
    status: Literal["READY"] = "READY"
    report_type: str = Field(..., alias="reportType")
    output_format: str = Field(..., alias="outputFormat")
    generated_at: datetime = Field(..., alias="generatedAt")
    download_url: str | None = Field(default=None, alias="downloadUrl")

    model_config = {"populate_by_name": True}


class IntegrationCapabilitiesResponse(BaseModel):
    source_service: str = Field("lotus-report", alias="sourceService")
    contract_version: str = Field(..., alias="contractVersion")
    policy_version: str = Field("ras-default-v1", alias="policyVersion")
    features: list[dict[str, str | bool]]
    workflows: list[dict[str, str | bool]]
    supported_input_modes: list[str] = Field(alias="supportedInputModes")

    model_config = {"populate_by_name": True}


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
            "PERFORMANCE, RISK_ANALYTICS, INCOME_AND_ACTIVITY, HOLDINGS, and TRANSACTIONS."
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
            "currency, sector, or geography where supported by upstream data."
        ),
        examples=[["asset_class", "currency"]],
    )
    look_through_mode: str | None = Field(
        default=None,
        description=(
            "Optional look-through handling requested by the caller. Current reporting behavior "
            "passes this as request context and does not invent look-through holdings."
        ),
        examples=["DIRECT"],
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
            "examples": [
                {
                    "as_of_date": "2026-04-22",
                    "sections": ["OVERVIEW", "ALLOCATION", "PERFORMANCE"],
                    "reporting_currency": "USD",
                    "allocation_dimensions": ["asset_class", "currency"],
                    "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
                },
                {
                    "as_of_date": "2026-04-22",
                    "sections": ["OVERVIEW", "HOLDINGS"],
                    "reporting_currency": "SGD",
                    "benchmark_code": "BMK_APAC_BALANCED",
                },
            ]
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
            "examples": [
                {
                    "contract_version": "v1",
                    "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
                    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                    "as_of_date": "2026-04-22",
                    "generated_at": "2026-04-22T09:00:00Z",
                    "readiness": {"status": "ready"},
                    "client_profile": {
                        "status": "present",
                        "identity": {
                            "client_id": "CIF_SG_000184",
                            "advisor_id": "RM_SG_001",
                            "booking_center_code": "SG",
                        },
                        "mandate_profile": {
                            "portfolio_type": "discretionary",
                            "objective": (
                                "Long-term real wealth growth with controlled income and liquidity."
                            ),
                            "risk_exposure": "balanced",
                            "investment_time_horizon": "7Y_PLUS",
                            "is_leverage_allowed": False,
                            "cost_basis_method": "FIFO",
                        },
                        "missing_fields": [],
                    },
                    "key_figures": {
                        "portfolio": {
                            "total_market_value": 1000000.0,
                            "reporting_currency": "USD",
                        },
                        "performance": {
                            "ytd_net_return_pct": -0.1557,
                            "total_contribution_ytd_pct": -0.1461,
                        },
                        "holdings": {
                            "total_unrealized_pnl_reporting_currency": 38970.67,
                            "total_unrealized_pnl_pct": 0.0295,
                        },
                    },
                    "report_coverage": {
                        "position_pnl_and_cost_basis": {
                            "status": "present",
                            "required": True,
                        },
                        "targets_guidelines_and_suitability": {
                            "status": "not_sourced",
                            "required": True,
                        },
                    },
                    "upstream_capability_audit": {
                        "status": "action_required",
                        "source_backed_capabilities": [
                            {
                                "capability_id": "holdings_pnl_cost_basis",
                                "source_service": "lotus-core",
                                "status": "present",
                            }
                        ],
                        "upstream_gaps": [
                            {
                                "capability_id": "targets_guidelines_suitability",
                                "owning_service": "lotus-advise / lotus-manage",
                                "status": "not_sourced",
                            }
                        ],
                        "report_side_findings": [],
                    },
                    "report_structure": {
                        "sequence": [
                            {
                                "order": 1,
                                "title": "Client, Mandate, And Meeting Context",
                                "section_ids": ["client_profile"],
                            }
                        ]
                    },
                    "advisor_briefing": {
                        "status": "ready",
                        "briefings": [
                            {
                                "briefing_id": "client_context",
                                "title": "Confirm client and mandate context",
                                "talking_points": [
                                    (
                                        "Client CIF_SG_000184 is reviewed under a balanced "
                                        "risk exposure."
                                    )
                                ],
                            }
                        ],
                    },
                    "ai_readiness": {
                        "status": "guarded_ready",
                        "mode": "grounded_assistance_metadata_only",
                        "blocked_features": [
                            "trade_recommendation",
                            "suitability_determination",
                            "client_profile_inference",
                        ],
                    },
                    "methodology": {
                        "performance_basis": "NET_AND_GROSS_WHERE_AVAILABLE",
                        "return_methodology": "time_weighted_return",
                    },
                    "evidence": {
                        "product_id": "lotus-report:ClientReportEvidencePack:v1",
                        "lineage_bundle_id": (
                            "lineage:lotus-report:portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22"
                        ),
                        "source_services": ["lotus-core"],
                    },
                    "client_sections": [
                        {
                            "section_id": "executive_summary",
                            "title": "Executive Review Summary",
                            "status": "ready",
                            "items": [{"total_market_value": 1000000.0}],
                        }
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
                                    "prompt": (
                                        "Confirm report readiness is ready for "
                                        "PB_SG_GLOBAL_BAL_001 as of 2026-04-22 with no "
                                        "unavailable client sections."
                                    ),
                                    "source_section_ids": ["executive_summary"],
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
                        "total_market_value": 1000000.0,
                        "total_cash": 50000.0,
                        "currency": "USD",
                    },
                },
                {
                    "contract_version": "v1",
                    "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
                    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                    "as_of_date": "2026-04-22",
                    "generated_at": "2026-04-22T09:00:00Z",
                    "readiness": {
                        "status": "partial",
                        "reason": "Performance section is unavailable for the selected request.",
                    },
                    "methodology": {
                        "performance_basis": "NET_AND_GROSS_WHERE_AVAILABLE",
                        "return_methodology": "time_weighted_return",
                    },
                    "evidence": {
                        "product_id": "lotus-report:ClientReportEvidencePack:v1",
                        "lineage_bundle_id": (
                            "lineage:lotus-report:portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22"
                        ),
                        "source_services": ["lotus-performance"],
                    },
                    "client_sections": [
                        {
                            "section_id": "performance_review",
                            "title": "Performance Review",
                            "status": "unavailable",
                            "reason_code": "source_unavailable",
                            "message": "Performance Review is unavailable for this request.",
                        },
                        {
                            "section_id": "transactions_appendix",
                            "title": "Transactions Appendix",
                            "status": "not_applicable",
                            "reason_code": "no_applicable_activity",
                            "message": (
                                "Transactions Appendix has no applicable activity for this request."
                            ),
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
                                    "prompt": (
                                        "Confirm report readiness is partial for "
                                        "PB_SG_GLOBAL_BAL_001 as of 2026-04-22 with unavailable "
                                        "client sections: Performance Review."
                                    ),
                                    "source_section_ids": ["performance_review"],
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
                    "performance": None,
                },
            ]
        },
    }
