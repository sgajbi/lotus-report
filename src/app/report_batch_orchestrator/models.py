from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.report_batch_orchestrator.contracts import BatchFrequency, BatchSelectorMode

BatchStatus = Literal["materialized"]
BatchItemStatus = Literal["materialized"]


class PortfolioBatchCandidate(BaseModel):
    portfolio_id: str = Field(
        ...,
        min_length=1,
        description="Portfolio identifier from lotus-core portfolio scope.",
        examples=["PB_SG_GLOBAL_BAL_001"],
    )
    tenant_id: str = Field(
        ...,
        min_length=1,
        description="Tenant that owns the portfolio in lotus-core.",
        examples=["tenant-sg"],
    )
    region: str = Field(
        ...,
        min_length=1,
        description="Region that owns the portfolio in lotus-core.",
        examples=["APAC"],
    )
    active: bool = Field(
        ...,
        description="Whether the portfolio is active and eligible for reporting.",
        examples=[True],
    )
    selected: bool = Field(
        False,
        description="Whether the caller-selected subset includes this portfolio.",
        examples=[True],
    )
    source_system: str = Field(
        "lotus-core",
        min_length=1,
        description="Authoritative source system for the portfolio candidate.",
        examples=["lotus-core"],
    )
    source_object: str = Field(
        "PortfolioScope",
        min_length=1,
        description="Authoritative source object or API contract for the portfolio candidate.",
        examples=["PortfolioScope"],
    )


class BatchCreateRequest(BaseModel):
    selector_mode: BatchSelectorMode = Field(
        ...,
        description="Portfolio selector mode used to materialize batch items.",
        examples=["explicit_portfolio_list"],
    )
    portfolio_ids: list[str] = Field(
        default_factory=list,
        description="Requested portfolio identifiers for explicit-list selection.",
        examples=[["PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002"]],
    )
    source_candidates: list[PortfolioBatchCandidate] = Field(
        default_factory=list,
        description="Portfolio candidates resolved from lotus-core before materialization.",
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date for all materialized batch items.",
        examples=["2026-04-22"],
    )
    requested_output_formats: list[str] = Field(
        default_factory=lambda: ["pdf"],
        description="Requested output formats for each future report job.",
        examples=[["pdf"]],
    )
    reporting_currency: str | None = Field(
        default=None,
        description="Reporting currency to pass into each future report job.",
        examples=["USD"],
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Report options that affect every materialized batch item.",
        examples=[{"sections": ["OVERVIEW", "PERFORMANCE"]}],
    )
    max_batch_size: int = Field(
        250,
        ge=1,
        le=1000,
        description="Maximum number of materialized items allowed for this request.",
        examples=[250],
    )


class MaterializedPortfolio(BaseModel):
    portfolio_id: str
    source_system: str
    source_object: str


class ReportBatchItemRecord(BaseModel):
    batch_item_id: str
    batch_id: str
    item_position: int
    portfolio_id: str
    item_idempotency_key: str
    status: BatchItemStatus
    source_system: str
    source_object: str
    created_at: datetime


class ReportBatchRecord(BaseModel):
    batch_id: str
    selector_mode: BatchSelectorMode
    tenant_id: str
    region: str
    materialized_portfolio_ids: list[str]
    as_of_date: date
    requested_output_formats: list[str]
    reporting_currency: str | None
    options: dict[str, Any]
    idempotency_key: str
    request_hash: str
    status: BatchStatus
    item_count: int
    created_at: datetime
    correlation_id: str
    trace_id: str
    items: list[ReportBatchItemRecord]


class BatchCycleRequest(BaseModel):
    frequency: BatchFrequency = Field(
        ...,
        description="Production cycle frequency to materialize.",
        examples=["monthly"],
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date for the cycle.",
        examples=["2026-04-30"],
    )
    explicit_period_start: date | None = Field(
        default=None,
        description="Required period start when frequency is explicit.",
        examples=["2026-04-01"],
    )
    explicit_period_end: date | None = Field(
        default=None,
        description="Required period end when frequency is explicit.",
        examples=["2026-04-30"],
    )
    template_id: str = Field(
        "portfolio-review",
        min_length=1,
        description="Report template identifier included in scheduled batch identity.",
        examples=["portfolio-review"],
    )
    template_version: str = Field(
        "v1",
        min_length=1,
        description="Report template version included in scheduled batch identity.",
        examples=["v1"],
    )
    render_package_version: str = Field(
        "portfolio-review.v1",
        min_length=1,
        description="Render package contract version included in scheduled batch identity.",
        examples=["portfolio-review.v1"],
    )


class BatchCycle(BaseModel):
    frequency: BatchFrequency
    period_start: date
    period_end: date
    as_of_date: date
    template_id: str
    template_version: str
    render_package_version: str
    idempotency_scope: str
