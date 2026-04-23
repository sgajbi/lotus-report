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


class ReportCallerContext(BaseModel):
    trigger_type: Literal["user", "system"] = "user"
    triggered_by: str
    caller_application: str
    tenant_id: str
    region: str
    booking_center_code: str | None = None
    role: str | None = None
    correlation_id: str
    trace_id: str


class ReportJobHandleResponse(BaseModel):
    report_request_id: str
    report_job_id: str
    status: ReportJobStatus
    status_url: str
    idempotency_key: str


class ReportJobStatusResponse(BaseModel):
    report_job_id: str
    report_request_id: str
    report_type: str
    portfolio_scope: dict[str, Any]
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


class ReportStatusEvent(BaseModel):
    status_event_id: str
    report_job_id: str
    from_status: ReportJobStatus | None = None
    to_status: ReportJobStatus
    event_type: str
    message: str | None = None
    actor: str
    created_at: datetime
    correlation_id: str
    trace_id: str


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
