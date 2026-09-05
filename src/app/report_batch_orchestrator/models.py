from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.report_batch_orchestrator.contracts import BatchFrequency, BatchSelectorMode
from app.reporting_jobs.models import ReportJobStatus

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
    booking_center_code: str | None = None
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


@dataclass(frozen=True)
class BatchPressureSnapshot:
    runnable_batches: int = 0
    active_batches: int = 0
    active_items: int = 0
    dispatch_ready_items: int = 0
    retry_ready_items: int = 0
    recovery_pending_items: int = 0


BATCH_CREATE_REQUEST_EXAMPLE: dict[str, Any] = {
    "selector_mode": "explicit_portfolio_list",
    "portfolio_ids": ["PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002"],
    "source_candidates": [
        {
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "active": True,
            "selected": True,
            "source_system": "lotus-core",
            "source_object": "PortfolioScope",
        },
        {
            "portfolio_id": "PB_SG_GLOBAL_BAL_002",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "active": True,
            "selected": True,
            "source_system": "lotus-core",
            "source_object": "PortfolioScope",
        },
    ],
    "as_of_date": "2026-04-22",
    "requested_output_formats": ["pdf"],
    "reporting_currency": "USD",
    "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
    "max_batch_size": 250,
}

BATCH_HANDLE_RESPONSE_EXAMPLE: dict[str, Any] = {
    "batch_id": "rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
    "status": "materialized",
    "status_url": "/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
    "idempotency_key": "batch-portfolio-review-2026-04-22",
    "item_count": 2,
}

BATCH_STATUS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "batch_id": "rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
    "selector_mode": "explicit_portfolio_list",
    "tenant_id": "tenant-sg",
    "region": "APAC",
    "materialized_portfolio_ids": ["PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002"],
    "as_of_date": "2026-04-22",
    "requested_output_formats": ["pdf"],
    "reporting_currency": "USD",
    "status": "materialized",
    "item_count": 2,
    "status_counts": {"materialized": 2},
    "items": [
        {
            "batch_item_id": "rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c",
            "item_position": 1,
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "status": "materialized",
            "report_job_id": None,
            "report_job_status": None,
            "archive_document_id": None,
            "attempt_count": 0,
            "retry_eligible": False,
            "next_retry_at": None,
            "last_error_category": None,
            "last_error_summary": None,
            "created_at": "2026-04-22T09:00:00Z",
            "started_at": None,
            "completed_at": None,
            "cancelled_at": None,
        }
    ],
    "created_at": "2026-04-22T09:00:00Z",
    "updated_at": "2026-04-22T09:00:00Z",
    "started_at": None,
    "completed_at": None,
    "cancelled_at": None,
    "failed_at": None,
    "correlation_id": "corr-batch-1",
    "trace_id": "trace-batch-1",
}

BATCH_ARCHIVED_STATUS_RESPONSE_EXAMPLE: dict[str, Any] = {
    **BATCH_STATUS_RESPONSE_EXAMPLE,
    "status": "completed",
    "item_count": 1,
    "status_counts": {"succeeded": 1},
    "items": [
        {
            **BATCH_STATUS_RESPONSE_EXAMPLE["items"][0],
            "status": "succeeded",
            "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "report_job_status": "archived",
            "archive_document_id": "doc_83ca965c50334c40a17d2b8cc94873a5",
            "started_at": "2026-04-22T09:01:00Z",
            "completed_at": "2026-04-22T09:04:00Z",
        }
    ],
    "completed_at": "2026-04-22T09:04:00Z",
}

BATCH_CONTROL_RESPONSE_EXAMPLE: dict[str, Any] = {
    "batch_id": "rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
    "status": "paused",
    "affected_count": 1,
    "status_url": "/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
}

BATCH_RECOVERY_RESPONSE_EXAMPLE: dict[str, Any] = {
    "batch_id": "rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
    "status": "running",
    "recovered_count": 1,
    "recovery_pending_item_ids": ["rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c"],
    "status_url": "/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
}

BATCH_ITEM_REPLAY_REQUEST_EXAMPLE: dict[str, Any] = {
    "reason": "Retry item after upstream service recovered.",
}

BATCH_ITEM_REPLAY_RESPONSE_EXAMPLE: dict[str, Any] = {
    "batch_id": "rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
    "batch_item_id": "rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c",
    "source_report_job_id": "rjob_failed_83ca965c50334c40a17d2b8cc94873a5",
    "replayed_report_job_id": "rjob_replay_83ca965c50334c40a17d2b8cc94873a5",
    "idempotency_key": "replay-rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c-upstream-retry-1",
    "item_status": "waiting_on_report_job",
    "report_job_status": "accepted",
    "retry_eligible": False,
    "status_url": (
        "/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c/items/"
        "rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c"
    ),
}

BATCH_WORKER_RUN_REQUEST_EXAMPLE: dict[str, Any] = {
    "worker_id": "lotus-report-batch-worker-1",
    "recover_expired_leases": True,
    "dispatch_policy": {
        "max_active_batches": 1,
        "max_active_items": 5,
        "max_active_upstream_jobs": 3,
        "max_active_render_jobs": 2,
        "max_active_archive_jobs": 2,
        "lease_seconds": 300,
    },
    "runtime_load": {
        "active_batches": 0,
        "active_items": 0,
        "active_upstream_jobs": 0,
        "active_render_jobs": 0,
        "active_archive_jobs": 0,
    },
}

BATCH_WORKER_RUN_RESPONSE_EXAMPLE: dict[str, Any] = {
    "batch_id": "rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
    "status": "completed",
    "batch_status_before": "materialized",
    "batch_status_after": "completed",
    "recovered_count": 0,
    "leased_count": 2,
    "dispatched_count": 2,
    "executed_count": 2,
    "report_job_ids": [
        "rjob_83ca965c50334c40a17d2b8cc94873a5",
        "rjob_1f7d965c50334c40a17d2b8cc94873a5",
    ],
    "back_pressure_reasons": [],
    "skipped_reason": None,
    "execution_results": [
        {
            "batch_item_id": "rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c",
            "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "item_status": "succeeded",
            "report_job_status": "archived",
            "failure_category": None,
            "retry_eligible": False,
        }
    ],
    "status_url": "/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c",
}


class BatchHandleResponse(BaseModel):
    batch_id: str = Field(
        ...,
        description="Opaque durable batch identifier used for batch status and control.",
        examples=["rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )
    status: BatchStatus = Field(
        ...,
        description="Current product-safe batch status.",
        examples=["materialized"],
    )
    status_url: str = Field(
        ...,
        description="Relative URL where callers can retrieve product-safe batch status.",
        examples=["/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key associated with this batch request.",
        examples=["batch-portfolio-review-2026-04-22"],
    )
    item_count: int = Field(
        ...,
        ge=0,
        description="Number of materialized portfolio report items in the batch.",
        examples=[2],
    )


class BatchItemStatusResponse(BaseModel):
    batch_item_id: str = Field(
        ...,
        description="Opaque durable batch item identifier.",
        examples=["rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c"],
    )
    item_position: int = Field(
        ...,
        ge=1,
        description="Deterministic item ordering within the batch.",
        examples=[1],
    )
    portfolio_id: str = Field(
        ...,
        description="Portfolio identifier represented by this batch item.",
        examples=["PB_SG_GLOBAL_BAL_001"],
    )
    status: BatchItemStatus = Field(
        ...,
        description="Current product-safe batch item status.",
        examples=["materialized"],
    )
    report_job_id: str | None = Field(
        default=None,
        description="Linked report job identifier after dispatch creates or reuses a job.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    report_job_status: ReportJobStatus | None = Field(
        default=None,
        description=(
            "Source Report lifecycle state for the currently linked report job. Null before "
            "linking or when a legacy or inconsistent link cannot be resolved."
        ),
        examples=["archived"],
    )
    archive_document_id: str | None = Field(
        default=None,
        description=(
            "Source-owned lotus-archive document identifier for the currently linked report "
            "job. Populated only when that job is archived; null while archival is pending, "
            "for a non-archived terminal job, or when the linked job cannot be resolved. "
            "Correction and replacement posture remains owned by archive metadata."
        ),
        examples=["doc_83ca965c50334c40a17d2b8cc94873a5"],
    )
    attempt_count: int = Field(
        0,
        ge=0,
        description="Number of recorded attempts for this batch item.",
        examples=[0],
    )
    retry_eligible: bool = Field(
        False,
        description="Whether this failed item is eligible for bounded retry.",
        examples=[False],
    )
    next_retry_at: datetime | None = Field(
        default=None,
        description="Earliest retry timestamp when bounded retry is available.",
        examples=["2026-04-22T09:05:00Z"],
    )
    last_error_category: str | None = Field(
        default=None,
        description="Support-safe machine-readable failure category for the latest item failure.",
        examples=["upstream_data_collection_failure"],
    )
    last_error_summary: str | None = Field(
        default=None,
        description="Support-safe summary for the latest item failure.",
        examples=["Source system returned a transient failure."],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the item was materialized.",
        examples=["2026-04-22T09:00:00Z"],
    )
    started_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when item execution first started.",
        examples=["2026-04-22T09:01:00Z"],
    )
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when item execution reached a terminal outcome.",
        examples=["2026-04-22T09:04:00Z"],
    )
    cancelled_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the item was cancelled.",
        examples=["2026-04-22T09:02:00Z"],
    )


class BatchStatusResponse(BaseModel):
    batch_id: str = Field(
        ...,
        description="Opaque durable batch identifier.",
        examples=["rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )
    selector_mode: BatchSelectorMode = Field(
        ...,
        description="Portfolio selector mode used to materialize the batch.",
        examples=["explicit_portfolio_list"],
    )
    tenant_id: str = Field(
        ...,
        description="Tenant ownership boundary for the batch.",
        examples=["tenant-sg"],
    )
    region: str = Field(
        ...,
        description="Regional ownership boundary for the batch.",
        examples=["APAC"],
    )
    materialized_portfolio_ids: list[str] = Field(
        ...,
        description="Portfolio identifiers materialized into durable batch items.",
        examples=[["PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002"]],
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date applied to all batch items.",
        examples=["2026-04-22"],
    )
    requested_output_formats: list[str] = Field(
        ...,
        description="Output formats requested for future item-level report jobs.",
        examples=[["pdf"]],
    )
    reporting_currency: str | None = Field(
        default=None,
        description="Reporting currency requested for item-level report jobs.",
        examples=["USD"],
    )
    status: BatchStatus = Field(
        ...,
        description="Current product-safe batch status.",
        examples=["materialized"],
    )
    item_count: int = Field(
        ...,
        ge=0,
        description="Number of materialized items in the batch.",
        examples=[2],
    )
    status_counts: dict[str, int] = Field(
        ...,
        description="Counts of batch items by current item status.",
        examples=[{"materialized": 2}],
    )
    items: list[BatchItemStatusResponse] = Field(
        ...,
        description="Ordered product-safe batch item status details.",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the batch was materialized.",
        examples=["2026-04-22T09:00:00Z"],
    )
    updated_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the batch was last updated.",
        examples=["2026-04-22T09:00:00Z"],
    )
    started_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the batch first started dispatch processing.",
        examples=["2026-04-22T09:01:00Z"],
    )
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the batch reached a completed outcome.",
        examples=["2026-04-22T09:10:00Z"],
    )
    cancelled_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the batch was cancelled.",
        examples=["2026-04-22T09:03:00Z"],
    )
    failed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the batch entered failed posture.",
        examples=["2026-04-22T09:04:00Z"],
    )
    correlation_id: str = Field(
        ...,
        description="End-to-end correlation identifier captured at batch creation.",
        examples=["corr-batch-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier captured at batch creation.",
        examples=["trace-batch-1"],
    )


class BatchControlResponse(BaseModel):
    batch_id: str = Field(
        ...,
        description="Opaque durable batch identifier.",
        examples=["rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )
    status: BatchStatus = Field(
        ...,
        description="Batch status after the control operation.",
        examples=["paused"],
    )
    affected_count: int = Field(
        ...,
        ge=0,
        description="Number of batch or item records affected by the control operation.",
        examples=[1],
    )
    status_url: str = Field(
        ...,
        description="Relative URL where callers can retrieve product-safe batch status.",
        examples=["/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )


class BatchRecoveryResponse(BaseModel):
    batch_id: str = Field(
        ...,
        description="Opaque durable batch identifier.",
        examples=["rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )
    status: BatchStatus = Field(
        ...,
        description="Batch status after expired-lease recovery.",
        examples=["running"],
    )
    recovered_count: int = Field(
        ...,
        ge=0,
        description="Number of expired unjobbed item leases moved to recovery-pending posture.",
        examples=[1],
    )
    recovery_pending_item_ids: list[str] = Field(
        default_factory=list,
        description="Batch item identifiers moved to recovery-pending posture.",
        examples=[["rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c"]],
    )
    status_url: str = Field(
        ...,
        description="Relative URL where callers can retrieve product-safe batch status.",
        examples=["/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )


class BatchItemReplayRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Support-safe business or operations reason for replaying this failed item.",
        examples=["Retry item after upstream service recovered."],
    )


class BatchItemReplayResponse(BaseModel):
    batch_id: str = Field(
        ...,
        description="Opaque durable batch identifier.",
        examples=["rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )
    batch_item_id: str = Field(
        ...,
        description="Opaque durable batch item identifier replayed by this command.",
        examples=["rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c"],
    )
    source_report_job_id: str | None = Field(
        default=None,
        description="Previously linked failed report job, when the item had one.",
        examples=["rjob_failed_83ca965c50334c40a17d2b8cc94873a5"],
    )
    replayed_report_job_id: str = Field(
        ...,
        description="New or reused replay-scoped report job linked to the batch item.",
        examples=["rjob_replay_83ca965c50334c40a17d2b8cc94873a5"],
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key for this batch-item replay command.",
        examples=["replay-rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c-upstream-retry-1"],
    )
    item_status: BatchItemStatus = Field(
        ...,
        description="Batch item status after replay relink.",
        examples=["waiting_on_report_job"],
    )
    report_job_status: str = Field(
        ...,
        description="Status of the replayed report job after relink.",
        examples=["accepted"],
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether the batch item remains retry eligible after replay relink.",
        examples=[False],
    )
    status_url: str = Field(
        ...,
        description="Relative URL where callers can retrieve product-safe batch item status.",
        examples=[
            "/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c/items/rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c"
        ],
    )


class BatchRuntimeLoad(BaseModel):
    active_batches: int = Field(0, ge=0)
    active_items: int = Field(0, ge=0)
    active_upstream_jobs: int = Field(0, ge=0)
    active_render_jobs: int = Field(0, ge=0)
    active_archive_jobs: int = Field(0, ge=0)


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


class BatchWorkerRunRequest(BaseModel):
    worker_id: str = Field(
        ...,
        min_length=1,
        description="Stable operator or service worker identifier recorded on leased items.",
        examples=["lotus-report-batch-worker-1"],
    )
    recover_expired_leases: bool = Field(
        True,
        description="Whether this bounded run should recover expired unjobbed item leases first.",
        examples=[True],
    )
    runtime_load: BatchRuntimeLoad | None = Field(
        default=None,
        description=(
            "Optional caller-supplied snapshot of active upstream, render, and archive work used "
            "for back-pressure decisions. Durable batch and item counts are derived from the "
            "ledger."
        ),
    )
    dispatch_policy: BatchDispatchPolicy | None = Field(
        default=None,
        description=(
            "Optional explicit dispatch policy for this bounded operator run. Omit to use the "
            "service default policy."
        ),
    )


class BatchWorkerItemExecutionResponse(BaseModel):
    batch_item_id: str = Field(
        ...,
        description="Opaque durable batch item identifier that was advanced by this run.",
        examples=["rbci_1a3b5c7d9e0f4a12a45f7a8d00bd129c"],
    )
    report_job_id: str = Field(
        ...,
        description="Report job identifier linked to the batch item.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    item_status: BatchItemStatus = Field(
        ...,
        description="Batch item status after this execution attempt.",
        examples=["succeeded"],
    )
    report_job_status: str = Field(
        ...,
        description="Report job status observed after this execution attempt.",
        examples=["archived"],
    )
    failure_category: str | None = Field(
        default=None,
        description="Product-safe failure category when item execution failed.",
        examples=["batch_execution_failed"],
    )
    retry_eligible: bool = Field(
        False,
        description="Whether the failed item remains eligible for bounded retry.",
        examples=[False],
    )


class BatchWorkerRunResponse(BaseModel):
    batch_id: str = Field(
        ...,
        description="Opaque durable batch identifier processed by this bounded run.",
        examples=["rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )
    status: BatchStatus = Field(
        ...,
        description="Batch status after the bounded worker run.",
        examples=["completed"],
    )
    batch_status_before: BatchStatus = Field(
        ...,
        description="Batch status observed before recovery, dispatch, or execution.",
        examples=["materialized"],
    )
    batch_status_after: BatchStatus = Field(
        ...,
        description="Batch status observed after recovery, dispatch, and execution.",
        examples=["completed"],
    )
    recovered_count: int = Field(
        ...,
        ge=0,
        description="Number of expired leases recovered before dispatch.",
        examples=[0],
    )
    leased_count: int = Field(
        ...,
        ge=0,
        description="Number of eligible items leased during dispatch.",
        examples=[2],
    )
    dispatched_count: int = Field(
        ...,
        ge=0,
        description="Number of report jobs created or reused for leased items.",
        examples=[2],
    )
    executed_count: int = Field(
        ...,
        ge=0,
        description="Number of waiting batch items advanced through report execution.",
        examples=[2],
    )
    report_job_ids: list[str] = Field(
        default_factory=list,
        description="Report job identifiers linked during this bounded run.",
        examples=[["rjob_83ca965c50334c40a17d2b8cc94873a5"]],
    )
    back_pressure_reasons: list[str] = Field(
        default_factory=list,
        description="Product-safe reasons dispatch was skipped or limited.",
        examples=[["max_active_render_jobs_reached"]],
    )
    skipped_reason: str | None = Field(
        default=None,
        description="Reason the batch was not runnable, when the run was skipped.",
        examples=["batch_not_runnable:paused"],
    )
    execution_results: list[BatchWorkerItemExecutionResponse] = Field(
        default_factory=list,
        description="Per-item execution outcomes produced by this bounded run.",
    )
    status_url: str = Field(
        ...,
        description="Relative URL where callers can retrieve product-safe batch status.",
        examples=["/reports/batches/rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )


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


class BatchCycle(BaseModel):
    frequency: BatchFrequency
    period_start: date
    period_end: date
    as_of_date: date
    idempotency_scope: str


class BatchDispatchResult(BaseModel):
    batch_id: str
    leased_count: int
    dispatched_count: int
    report_job_ids: list[str]
    back_pressure_reasons: list[str] = Field(default_factory=list)
