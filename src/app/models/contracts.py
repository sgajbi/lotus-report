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
    as_of_date: date = Field(..., alias="asOfDate")
    sections: list[str] | None = None
    reporting_currency: str | None = Field(default=None, alias="reportingCurrency")
    allocation_dimensions: list[str] | None = Field(default=None, alias="allocationDimensions")
    look_through_mode: str | None = Field(default=None, alias="lookThroughMode")

    model_config = {
        "extra": "allow",
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "as_of_date": "2026-04-22",
                    "sections": ["OVERVIEW", "ALLOCATION", "PERFORMANCE"],
                    "reporting_currency": "USD",
                    "allocation_dimensions": ["asset_class", "currency"],
                },
                {
                    "asOfDate": "2026-04-22",
                    "sections": ["OVERVIEW", "HOLDINGS"],
                    "reportingCurrency": "SGD",
                },
            ]
        },
    }


class PortfolioReviewReadiness(BaseModel):
    status: Literal["ready", "partial", "unavailable"]
    reason: str | None = None


class PortfolioReviewSection(BaseModel):
    section_id: str
    title: str
    status: Literal["ready", "partial", "unavailable", "omitted_by_request", "not_applicable"]
    reason_code: str | None = None
    message: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


class PortfolioReviewReportResponse(BaseModel):
    contract_version: str = "v1"
    report_id: str
    portfolio_id: str
    as_of_date: date
    generated_at: datetime
    readiness: PortfolioReviewReadiness
    client_sections: list[PortfolioReviewSection] = Field(default_factory=list)
    advisor_sections: list[PortfolioReviewSection] = Field(default_factory=list)
    overview: dict[str, Any] | None = None
    allocation: dict[str, Any] | None = None
    performance: dict[str, Any] | None = None
    risk_analytics: dict[str, Any] | None = Field(default=None, alias="riskAnalytics")
    income_and_activity: dict[str, Any] | None = Field(default=None, alias="incomeAndActivity")
    holdings: dict[str, Any] | None = None
    transactions: dict[str, Any] | None = None

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
                    "client_sections": [
                        {
                            "section_id": "executive_summary",
                            "title": "Executive Review Summary",
                            "status": "ready",
                            "items": [{"total_market_value": 1000000.0}],
                        }
                    ],
                    "advisor_sections": [],
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
                    "client_sections": [
                        {
                            "section_id": "performance_review",
                            "title": "Performance Review",
                            "status": "unavailable",
                            "reason_code": "source_unavailable",
                            "message": "Performance Review is unavailable for this request.",
                        }
                    ],
                    "advisor_sections": [],
                    "performance": None,
                },
            ]
        },
    }
