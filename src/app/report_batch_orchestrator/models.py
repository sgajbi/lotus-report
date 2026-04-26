from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.report_batch_orchestrator.contracts import BatchFrequency, BatchSelectorMode

BatchStatus = Literal[
    "materialized",
    "running",
    "paused",
    "cancelled",
    "completed",
    "completed_with_failures",
    "failed",
]
BatchItemStatus = Literal[
    "materialized",
    "leased",
    "waiting_on_report_job",
    "succeeded",
    "failed_retryable",
    "failed_terminal",
    "cancelled",
    "recovery_pending",
]


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
    report_job_id: str | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_acquired_at: datetime | None = None
    lease_expires_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    dispatched_at: datetime | None = None
    attempt_count: int = 0
    retry_eligible: bool = False
    next_retry_at: datetime | None = None
    last_error_category: str | None = None
    last_error_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


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
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    failed_at: datetime | None = None
    correlation_id: str
    trace_id: str
    items: list[ReportBatchItemRecord]


class BatchRetryPolicy(BaseModel):
    max_attempts: int = Field(
        3,
        ge=1,
        description="Maximum attempts allowed before a retryable batch item becomes terminal.",
        examples=[3],
    )


class BatchControlResult(BaseModel):
    batch_id: str
    affected_count: int
    batch_status: BatchStatus


class BatchRecoveryResult(BaseModel):
    batch_id: str
    recovered_count: int
    recovery_pending_item_ids: list[str] = Field(default_factory=list)


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


class BatchDispatchPolicy(BaseModel):
    max_active_batches: int = Field(
        1,
        ge=1,
        description="Maximum number of active batches allowed for this dispatch attempt.",
        examples=[1],
    )
    max_active_items: int = Field(
        5,
        ge=1,
        description="Maximum number of items leased by one dispatch attempt.",
        examples=[5],
    )
    max_active_upstream_jobs: int = Field(
        3,
        ge=1,
        description="Maximum active upstream data-collection work allowed before back-pressure.",
        examples=[3],
    )
    max_active_render_jobs: int = Field(
        2,
        ge=1,
        description="Maximum active render work allowed before back-pressure.",
        examples=[2],
    )
    max_active_archive_jobs: int = Field(
        2,
        ge=1,
        description="Maximum active archive work allowed before back-pressure.",
        examples=[2],
    )
    lease_seconds: int = Field(
        300,
        ge=1,
        description="Lease duration for in-flight batch item dispatch.",
        examples=[300],
    )


class BatchRuntimeLoad(BaseModel):
    active_batches: int = Field(0, ge=0)
    active_items: int = Field(0, ge=0)
    active_upstream_jobs: int = Field(0, ge=0)
    active_render_jobs: int = Field(0, ge=0)
    active_archive_jobs: int = Field(0, ge=0)


class BatchDispatchResult(BaseModel):
    batch_id: str
    leased_count: int
    dispatched_count: int
    report_job_ids: list[str]
    back_pressure_reasons: list[str] = Field(default_factory=list)
