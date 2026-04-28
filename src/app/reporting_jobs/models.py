from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ReportJobStatus = Literal[
    "accepted",
    "queued",
    "collecting_data",
    "data_ready",
    "rendering",
    "completed",
    "archiving",
    "archived",
    "completed_with_warnings",
    "failed",
    "cancelled",
]

ReportFailureCategory = Literal[
    "entitlement_failed",
    "validation_failed",
    "upstream_data_failed",
    "data_incomplete",
    "render_validation_failed",
    "render_conflict",
    "render_execution_failed",
    "archive_validation_failed",
    "archive_conflict",
    "archive_storage_failed",
    "archive_execution_failed",
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
            "Requested output formats. The current wave supports JSON-only jobs that stop at "
            "`data_ready` and PDF jobs that submit a governed render package to lotus-render."
        ),
        examples=[["json"], ["pdf"]],
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
                "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
            }
        ],
    )


PORTFOLIO_REVIEW_JOB_REQUEST_EXAMPLE: dict[str, Any] = {
    "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
    "as_of_date": "2026-04-22",
    "requested_output_formats": ["pdf"],
    "reporting_currency": "USD",
    "options": {
        "sections": ["OVERVIEW", "PERFORMANCE", "RISK_ANALYTICS"],
        "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
    },
}


REPORT_JOB_HANDLE_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_request_id": "rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "status": "archived",
    "status_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5",
    "idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
}


REPORT_JOB_STATUS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "report_request_id": "rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
    "report_type": "portfolio_review",
    "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
    "status": "archived",
    "failure_category": None,
    "failure_message": None,
    "current_step": "archived",
    "retry_eligible": False,
    "cancel_requested": False,
    "created_at": "2026-04-22T09:00:00Z",
    "updated_at": "2026-04-22T09:00:03Z",
    "started_at": None,
    "completed_at": "2026-04-22T09:00:03Z",
    "cancelled_at": None,
    "correlation_id": "corr-portfolio-review-1",
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    "render": {
        "render_job_id": "rdr_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
        "output_format": "pdf",
        "template_id": "portfolio-review",
        "template_version": "v1",
        "artifact_sha256": "sha256:artifact-portfolio-review",
        "bounded_determinism_fingerprint": "typst-0.14.2:a4c71e5d",
        "runtime_engine": "typst",
        "runtime_engine_version": "0.14.2",
        "render_duration_ms": 812,
    },
    "archive": {
        "archive_request_id": "arch_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
        "document_id": "doc_83ca965c50334c40a17d2b8cc94873a5",
        "completed_at": "2026-04-22T09:00:04Z",
    },
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

REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "status": REPORT_JOB_STATUS_RESPONSE_EXAMPLE,
    "event_count": 4,
    "latest_event": {
        **REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE["events"][0],
        "to_status": "archived",
        "event_type": "job_archived",
        "message": "Report artifact archived by lotus-archive.",
    },
    "snapshot": {
        "snapshot_id": "rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf",
        "snapshot_hash": "sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd",
        "supportability_status": "complete",
        "completeness_status": "complete",
        "captured_at": "2026-04-22T09:00:03Z",
    },
    "lineage": {
        "upstream_call_count": 3,
        "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
        "supportability_status": "complete",
        "completeness_status": "complete",
        "failure_categories": [],
    },
    "render": REPORT_JOB_STATUS_RESPONSE_EXAMPLE["render"],
    "archive": REPORT_JOB_STATUS_RESPONSE_EXAMPLE["archive"],
    "diagnostic_flags": [],
    "operation_links": {
        "status_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5",
        "events_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/events",
        "snapshot_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/snapshot",
        "lineage_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/lineage",
    },
}

REPORT_JOB_LIST_FILTERS_EXAMPLE: dict[str, Any] = {
    "tenant_id": "tenant-sg",
    "region": "APAC",
    "status": "archived",
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
            "status": "archived",
            "failure_category": None,
            "current_step": "archived",
            "retry_eligible": False,
            "cancel_requested": False,
            "idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
            "correlation_id": "corr-portfolio-review-1",
            "created_at": "2026-04-22T09:00:00Z",
            "updated_at": "2026-04-22T09:00:03Z",
            "render": {
                "render_job_id": "rdr_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
                "output_format": "pdf",
                "template_id": "portfolio-review",
                "template_version": "v1",
                "artifact_sha256": "sha256:artifact-portfolio-review",
                "bounded_determinism_fingerprint": "typst-0.14.2:a4c71e5d",
                "runtime_engine": "typst",
                "runtime_engine_version": "0.14.2",
                "render_duration_ms": 812,
            },
            "archive": {
                "archive_request_id": "arch_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
                "document_id": "doc_83ca965c50334c40a17d2b8cc94873a5",
                "completed_at": "2026-04-22T09:00:04Z",
            },
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
    "report_snapshot_not_found": {
        "detail": {
            "code": "report_snapshot_not_found",
            "message": "Report snapshot was not found.",
        }
    },
    "report_lineage_store_unavailable": {
        "detail": {
            "code": "report_lineage_store_unavailable",
            "message": "Report lineage diagnostics are temporarily unavailable.",
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


class ReportJobRenderInfo(BaseModel):
    render_job_id: str | None = Field(
        default=None,
        description="Opaque lotus-render job identifier when rendering was requested.",
        examples=["rdr_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf"],
    )
    output_format: str | None = Field(
        default=None,
        description="Rendered output format when rendering was requested.",
        examples=["pdf"],
    )
    template_id: str | None = Field(
        default=None,
        description="Template identifier used by lotus-render.",
        examples=["portfolio-review"],
    )
    template_version: str | None = Field(
        default=None,
        description="Template version used by lotus-render.",
        examples=["v1"],
    )
    artifact_sha256: str | None = Field(
        default=None,
        description="Rendered artifact hash when rendering completed successfully.",
        examples=["sha256:2f817e5d665db6c709e1a9f2332ff7fa609d7304c55ba921f97d9b2d71b0679d"],
    )
    bounded_determinism_fingerprint: str | None = Field(
        default=None,
        description="Bounded determinism fingerprint when rendering completed successfully.",
        examples=["376a56c2eae1ccd6a1e09f8c51b190d098b7b7221e266c86dcc524132b745140"],
    )
    runtime_engine: str | None = Field(
        default=None,
        description="Render engine reported by lotus-render.",
        examples=["typst"],
    )
    runtime_engine_version: str | None = Field(
        default=None,
        description="Render engine version reported by lotus-render.",
        examples=["0.14.2"],
    )
    render_duration_ms: int | None = Field(
        default=None,
        description="Measured render duration in milliseconds when available.",
        examples=[842],
    )


class ReportJobArchiveInfo(BaseModel):
    archive_request_id: str | None = Field(
        default=None,
        description="Idempotent archive request identifier used for the rendered document.",
        examples=["arch_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf"],
    )
    document_id: str | None = Field(
        default=None,
        description="Archived document identifier returned by lotus-archive.",
        examples=["doc_83ca965c50334c40a17d2b8cc94873a5"],
    )
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when lotus-archive confirmed document archival.",
        examples=["2026-04-22T09:00:04Z"],
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
    render: ReportJobRenderInfo | None = Field(
        default=None,
        description="Support-safe render metadata when a rendered artifact was requested.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="Support-safe archive metadata when the rendered artifact was archived.",
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


class ReportJobSnapshotDiagnostics(BaseModel):
    snapshot_id: str = Field(
        ...,
        description="Opaque durable snapshot identifier associated with the report job.",
        examples=["rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf"],
    )
    snapshot_hash: str = Field(
        ...,
        description="Canonical hash of the captured snapshot payload; raw payload is not returned.",
        examples=["sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"],
    )
    supportability_status: str = Field(
        ...,
        description="Supportability posture recorded on the durable snapshot.",
        examples=["complete"],
    )
    completeness_status: str = Field(
        ...,
        description="Completeness posture recorded on the durable snapshot.",
        examples=["complete"],
    )
    captured_at: datetime = Field(
        ...,
        description="UTC timestamp when snapshot capture completed.",
        examples=["2026-04-22T09:00:03Z"],
    )


class ReportJobLineageDiagnostics(BaseModel):
    upstream_call_count: int = Field(
        ...,
        description="Number of durable upstream-call evidence rows linked to the snapshot.",
        examples=[3],
    )
    source_services: list[str] = Field(
        ...,
        description="Bounded service names observed in upstream lineage evidence.",
        examples=[["lotus-core", "lotus-performance", "lotus-risk"]],
    )
    supportability_status: str = Field(
        ...,
        description="Aggregated supportability posture from the durable snapshot summary.",
        examples=["complete"],
    )
    completeness_status: str = Field(
        ...,
        description="Aggregated completeness posture from the durable snapshot summary.",
        examples=["complete"],
    )
    failure_categories: list[str] = Field(
        ...,
        description="Distinct non-empty upstream failure categories excluding normal `none` rows.",
        examples=[["upstream_unavailable"]],
    )


class ReportJobOperationLinks(BaseModel):
    status_url: str = Field(
        ...,
        description="Relative URL for the source-backed report job status contract.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    events_url: str = Field(
        ...,
        description="Relative URL for append-only report job lifecycle events.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/events"],
    )
    snapshot_url: str | None = Field(
        default=None,
        description="Relative URL for the durable snapshot when snapshot evidence exists.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/snapshot"],
    )
    lineage_url: str | None = Field(
        default=None,
        description="Relative URL for durable upstream lineage when snapshot evidence exists.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/lineage"],
    )


class ReportJobDiagnosticsResponse(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Opaque durable report job identifier inspected by the diagnostics view.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    status: ReportJobStatusResponse = Field(
        ...,
        description="Source-backed product-safe job status and render/archive summary.",
        examples=[REPORT_JOB_STATUS_RESPONSE_EXAMPLE],
    )
    event_count: int = Field(
        ...,
        description="Count of append-only lifecycle events recorded for the job.",
        examples=[4],
    )
    latest_event: ReportStatusEvent | None = Field(
        default=None,
        description="Most recent lifecycle event when event history exists.",
    )
    snapshot: ReportJobSnapshotDiagnostics | None = Field(
        default=None,
        description="Support-safe snapshot posture without raw snapshot payload or storage refs.",
    )
    lineage: ReportJobLineageDiagnostics | None = Field(
        default=None,
        description="Support-safe lineage summary without request or response payloads.",
    )
    render: ReportJobRenderInfo | None = Field(
        default=None,
        description="Support-safe render metadata copied from the report job ledger.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="Support-safe archive handoff and document identifiers from the job ledger.",
    )
    diagnostic_flags: list[str] = Field(
        ...,
        description="Machine-readable support flags such as `snapshot_not_captured`.",
        examples=[["snapshot_not_captured"]],
    )
    operation_links: ReportJobOperationLinks = Field(
        ...,
        description="Related source-backed operator endpoints for deeper evidence review.",
        examples=[REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE["operation_links"]],
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
    render: ReportJobRenderInfo | None = Field(
        default=None,
        description="Support-safe render summary when a rendered artifact was requested.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="Support-safe archive summary when the rendered artifact was archived.",
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
    render_job_id: str | None = None
    render_output_format: str | None = None
    render_template_id: str | None = None
    render_template_version: str | None = None
    render_artifact_sha256: str | None = None
    render_bounded_determinism_fingerprint: str | None = None
    render_runtime_engine: str | None = None
    render_runtime_engine_version: str | None = None
    render_duration_ms: int | None = None
    archive_request_id: str | None = None
    archive_document_id: str | None = None
    archive_completed_at: datetime | None = None
