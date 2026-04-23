from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ReportJobStatus = Literal[
    "accepted",
    "queued",
    "collecting_data",
    "data_ready",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
]

ReportFailureCategory = Literal[
    "entitlement_failed",
    "validation_failed",
    "upstream_data_failed",
    "data_incomplete",
    "timeout",
    "cancelled",
    "operator_intervention_required",
]


class PortfolioReviewJobRequest(BaseModel):
    portfolio_scope: dict[str, Any] = Field(
        ...,
        description="Portfolio scope for the report job. First wave supports portfolio_ids.",
        examples=[{"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}],
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date for the report job.",
        examples=["2026-04-22"],
    )
    requested_output_formats: list[str] = Field(
        default_factory=lambda: ["json"],
        description=(
            "Requested output formats. The first job-ledger wave accepts job intent only; "
            "PDF is not rendered."
        ),
        examples=[["json"]],
    )
    reporting_currency: str | None = Field(
        default=None,
        description="Optional reporting currency used in the report request hash.",
        examples=["USD"],
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Output-affecting report options included in idempotency hashing.",
        examples=[
            {
                "sections": ["OVERVIEW", "PERFORMANCE"],
                "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
            }
        ],
    )


PORTFOLIO_REVIEW_JOB_REQUEST_EXAMPLE: dict[str, Any] = {
    "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
    "as_of_date": "2026-04-22",
    "requested_output_formats": ["json"],
    "reporting_currency": "USD",
    "options": {
        "sections": ["OVERVIEW", "PERFORMANCE", "RISK_ANALYTICS"],
        "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
    },
}


REPORT_JOB_HANDLE_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_request_id": "rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "status": "accepted",
    "status_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5",
    "idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
}


REPORT_JOB_STATUS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "report_request_id": "rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
    "report_type": "portfolio_review",
    "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
    "status": "accepted",
    "failure_category": None,
    "failure_message": None,
    "current_step": "accepted",
    "retry_eligible": False,
    "cancel_requested": False,
    "created_at": "2026-04-22T09:00:00Z",
    "updated_at": "2026-04-22T09:00:00Z",
    "started_at": None,
    "completed_at": None,
    "cancelled_at": None,
    "correlation_id": "corr-portfolio-review-1",
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
}


REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "events": [
        {
            "status_event_id": "rse_d7e9c3b87d864b098997d4fe5bd2de2a",
            "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "from_status": None,
            "to_status": "accepted",
            "event_type": "job_accepted",
            "message": "Portfolio review report job accepted.",
            "actor": "advisor-123",
            "created_at": "2026-04-22T09:00:00Z",
            "correlation_id": "corr-portfolio-review-1",
            "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        }
    ],
}

REPORT_JOB_LIST_FILTERS_EXAMPLE: dict[str, Any] = {
    "tenant_id": "tenant-sg",
    "region": "APAC",
    "status": "accepted",
    "report_type": "portfolio_review",
    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
    "as_of_date": "2026-04-22",
    "idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
    "correlation_id": "corr-portfolio-review-1",
    "created_from": "2026-04-22T00:00:00Z",
    "created_to": "2026-04-23T00:00:00Z",
    "limit": 25,
}

REPORT_JOB_LIST_RESPONSE_EXAMPLE: dict[str, Any] = {
    "count": 1,
    "applied_filters": REPORT_JOB_LIST_FILTERS_EXAMPLE,
    "items": [
        {
            "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "report_request_id": "rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
            "report_type": "portfolio_review",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
            "as_of_date": "2026-04-22",
            "status": "accepted",
            "failure_category": None,
            "current_step": "accepted",
            "retry_eligible": False,
            "cancel_requested": False,
            "idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
            "correlation_id": "corr-portfolio-review-1",
            "created_at": "2026-04-22T09:00:00Z",
            "updated_at": "2026-04-22T09:00:00Z",
        }
    ],
}

API_ERROR_RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "missing_idempotency_key": {
        "detail": {
            "code": "missing_idempotency_key",
            "message": "Idempotency-Key is required.",
        }
    },
    "missing_caller_context": {
        "detail": {
            "code": "missing_caller_context",
            "message": "Required caller context headers are missing.",
            "missing_headers": [
                "X-Actor-Id",
                "X-Caller-Application",
                "X-Tenant-Id",
                "X-Region",
            ],
        }
    },
    "idempotency_conflict": {
        "detail": {
            "code": "idempotency_conflict",
            "message": "Idempotency-Key was reused with a different report request.",
        }
    },
    "report_job_not_found": {
        "detail": {
            "code": "report_job_not_found",
            "message": "Report job was not found.",
        }
    },
    "report_job_cannot_be_cancelled": {
        "detail": {
            "code": "report_job_cannot_be_cancelled",
            "message": "Report job can no longer be cancelled.",
        }
    },
    "invalid_report_job_filters": {
        "detail": {
            "code": "invalid_report_job_filters",
            "message": "At least one supported job-search filter is required.",
        }
    },
}


class ReportCallerContext(BaseModel):
    trigger_type: Literal["user", "system"] = Field(
        "user",
        description="Origin category for the request: user-driven or system-driven.",
        examples=["user"],
    )
    triggered_by: str = Field(
        ...,
        description="Authenticated actor or system principal that triggered the request.",
        examples=["advisor-123"],
    )
    caller_application: str = Field(
        ...,
        description="Calling Lotus application at the governed boundary.",
        examples=["lotus-gateway"],
    )
    tenant_id: str = Field(
        ...,
        description="Tenant identifier for entitlement and audit.",
        examples=["tenant-sg"],
    )
    region: str = Field(
        ...,
        description="Operating region for segregation and audit.",
        examples=["APAC"],
    )
    booking_center_code: str | None = Field(
        default=None,
        description="Optional booking center associated with the caller or request.",
        examples=["SG"],
    )
    role: str | None = Field(
        default=None,
        description="Optional caller role captured for audit and support diagnostics.",
        examples=["advisor"],
    )
    correlation_id: str = Field(
        ...,
        description="End-to-end correlation identifier propagated through Lotus services.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier propagated through Lotus services.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ApiErrorDetail(BaseModel):
    code: str = Field(
        ...,
        description="Machine-readable error code for deterministic client handling.",
        examples=["report_job_not_found"],
    )
    message: str = Field(
        ...,
        description="Support-safe error message explaining the failure.",
        examples=["Report job was not found."],
    )
    missing_headers: list[str] | None = Field(
        default=None,
        description="Header names that must be supplied when caller context is incomplete.",
        examples=[["X-Actor-Id", "X-Tenant-Id", "X-Region"]],
    )


class ApiErrorResponse(BaseModel):
    detail: ApiErrorDetail = Field(
        ...,
        description="Structured API error payload for product and operator consumers.",
        examples=[API_ERROR_RESPONSE_EXAMPLES["report_job_not_found"]["detail"]],
    )


class ReportJobHandleResponse(BaseModel):
    report_request_id: str = Field(
        ...,
        description="Opaque durable report request identifier stored in the ledger.",
        examples=["rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c"],
    )
    report_job_id: str = Field(
        ...,
        description="Opaque durable report job identifier used for status and cancellation.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    status: ReportJobStatus = Field(
        ...,
        description="Current product-safe job status.",
        examples=["accepted"],
    )
    status_url: str = Field(
        ...,
        description="Relative URL where callers can retrieve product-safe job status.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key associated with this request.",
        examples=["portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22"],
    )


class ReportJobStatusResponse(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Opaque durable report job identifier.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    report_request_id: str = Field(
        ...,
        description="Opaque durable report request identifier linked to the job.",
        examples=["rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c"],
    )
    report_type: str = Field(
        ...,
        description="Report type handled by this job ledger record.",
        examples=["portfolio_review"],
    )
    portfolio_scope: dict[str, Any] = Field(
        ...,
        description="Portfolio scope submitted for the report job.",
        examples=[{"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}],
    )
    status: ReportJobStatus = Field(
        ...,
        description="Current product-safe job status.",
        examples=["accepted"],
    )
    failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Machine-readable failure category when the job is failed or cancelled.",
        examples=["cancelled"],
    )
    failure_message: str | None = Field(
        default=None,
        description="Support-safe failure message suitable for operators and product consumers.",
        examples=["Report job cancelled before render or archive processing."],
    )
    current_step: str = Field(
        ...,
        description="Current lifecycle step for support diagnostics.",
        examples=["accepted"],
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether retry or replay is currently permitted by the ledger.",
        examples=[False],
    )
    cancel_requested: bool = Field(
        ...,
        description="Whether cancellation has been requested and recorded.",
        examples=[False],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the job was created.",
        examples=["2026-04-22T09:00:00Z"],
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp for the latest job state update.",
        examples=["2026-04-22T09:00:00Z"],
    )
    started_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when processing started, if processing has begun.",
        examples=["2026-04-22T09:00:05Z"],
    )
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the job completed, if complete.",
        examples=["2026-04-22T09:01:15Z"],
    )
    cancelled_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the job was cancelled, if cancelled.",
        examples=["2026-04-22T09:00:30Z"],
    )
    correlation_id: str = Field(
        ...,
        description="Correlation identifier captured at request creation.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier captured at request creation.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ReportStatusEvent(BaseModel):
    status_event_id: str = Field(
        ...,
        description="Opaque append-only status event identifier.",
        examples=["rse_d7e9c3b87d864b098997d4fe5bd2de2a"],
    )
    report_job_id: str = Field(
        ...,
        description="Report job identifier associated with this event.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    from_status: ReportJobStatus | None = Field(
        default=None,
        description="Previous job status when this event is a transition.",
        examples=["accepted"],
    )
    to_status: ReportJobStatus = Field(
        ...,
        description="New job status recorded by this event.",
        examples=["cancelled"],
    )
    event_type: str = Field(
        ...,
        description="Machine-readable lifecycle event type.",
        examples=["job_cancelled"],
    )
    message: str | None = Field(
        default=None,
        description="Support-safe event message.",
        examples=["Report job cancelled before render or archive processing."],
    )
    actor: str = Field(
        ...,
        description="Actor or system principal that caused the event.",
        examples=["advisor-123"],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the status event was appended.",
        examples=["2026-04-22T09:00:30Z"],
    )
    correlation_id: str = Field(
        ...,
        description="Correlation identifier associated with this status event.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier associated with this status event.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ReportJobStatusEventsResponse(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Report job identifier whose event history is returned.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    events: list[ReportStatusEvent] = Field(
        ...,
        description="Append-only lifecycle events ordered by creation time.",
        examples=[REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE["events"]],
    )


class ReportJobListFilters(BaseModel):
    tenant_id: str | None = Field(
        default=None,
        description="Tenant filter used to isolate jobs for one tenant scope.",
        examples=["tenant-sg"],
    )
    region: str | None = Field(
        default=None,
        description="Region filter used to isolate jobs for one operating region.",
        examples=["APAC"],
    )
    status: ReportJobStatus | None = Field(
        default=None,
        description="Current job-status filter.",
        examples=["accepted"],
    )
    report_type: str | None = Field(
        default=None,
        description="Report-type filter for the job search.",
        examples=["portfolio_review"],
    )
    portfolio_id: str | None = Field(
        default=None,
        description="Portfolio identifier contained in the submitted portfolio scope.",
        examples=["PB_SG_GLOBAL_BAL_001"],
    )
    as_of_date: date | None = Field(
        default=None,
        description="Business as-of date filter for the report request.",
        examples=["2026-04-22"],
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Idempotency key filter for duplicate-request diagnostics.",
        examples=["portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22"],
    )
    correlation_id: str | None = Field(
        default=None,
        description="Correlation identifier filter for end-to-end operational tracing.",
        examples=["corr-portfolio-review-1"],
    )
    created_from: datetime | None = Field(
        default=None,
        description="Inclusive lower UTC bound for report-request creation time.",
        examples=["2026-04-22T00:00:00Z"],
    )
    created_to: datetime | None = Field(
        default=None,
        description="Inclusive upper UTC bound for report-request creation time.",
        examples=["2026-04-23T00:00:00Z"],
    )
    limit: int = Field(
        default=25,
        description="Maximum number of jobs returned by this bounded first-wave search.",
        examples=[25],
    )


class ReportJobListItem(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Opaque durable report job identifier.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    report_request_id: str = Field(
        ...,
        description="Opaque durable report request identifier linked to the job.",
        examples=["rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c"],
    )
    report_type: str = Field(
        ...,
        description="Report type handled by the job.",
        examples=["portfolio_review"],
    )
    tenant_id: str = Field(
        ...,
        description="Tenant identifier captured when the request was created.",
        examples=["tenant-sg"],
    )
    region: str = Field(
        ...,
        description="Operating region captured when the request was created.",
        examples=["APAC"],
    )
    portfolio_scope: dict[str, Any] = Field(
        ...,
        description="Submitted portfolio scope for the report job.",
        examples=[{"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}],
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date submitted for the report job.",
        examples=["2026-04-22"],
    )
    status: ReportJobStatus = Field(
        ...,
        description="Current product-safe report job status.",
        examples=["accepted"],
    )
    failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Machine-readable failure category when the job failed or was cancelled.",
        examples=[None],
    )
    current_step: str = Field(
        ...,
        description="Current lifecycle step for support diagnostics.",
        examples=["accepted"],
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether retry or replay is currently permitted.",
        examples=[False],
    )
    cancel_requested: bool = Field(
        ...,
        description="Whether cancellation has been requested and recorded.",
        examples=[False],
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key associated with the job.",
        examples=["portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22"],
    )
    correlation_id: str = Field(
        ...,
        description="Correlation identifier captured when the request was created.",
        examples=["corr-portfolio-review-1"],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the job was created.",
        examples=["2026-04-22T09:00:00Z"],
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp when the job was last updated.",
        examples=["2026-04-22T09:00:00Z"],
    )


class ReportJobListResponse(BaseModel):
    count: int = Field(
        ...,
        description="Number of jobs returned in this bounded response.",
        examples=[1],
    )
    applied_filters: ReportJobListFilters = Field(
        ...,
        description="Normalized filters applied to the job search.",
        examples=[REPORT_JOB_LIST_FILTERS_EXAMPLE],
    )
    items: list[ReportJobListItem] = Field(
        ...,
        description="Bounded list of support-safe report job summaries.",
        examples=[REPORT_JOB_LIST_RESPONSE_EXAMPLE["items"]],
    )


class ReportJobLedgerRecord(BaseModel):
    request_id: str
    job_id: str
    report_type: str
    portfolio_scope: dict[str, Any]
    requested_output_formats: list[str]
    as_of_date: date
    reporting_currency: str | None = None
    options: dict[str, Any]
    trigger_type: str
    triggered_by: str
    caller_application: str
    tenant_id: str
    region: str
    booking_center_code: str | None = None
    role: str | None = None
    idempotency_key: str
    request_hash: str
    status: ReportJobStatus
    failure_category: ReportFailureCategory | None = None
    failure_message: str | None = None
    current_step: str
    retry_eligible: bool
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    correlation_id: str
    trace_id: str
