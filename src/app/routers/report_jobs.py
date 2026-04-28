from time import perf_counter
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status

from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import (
    API_ERROR_RESPONSE_EXAMPLES,
    PORTFOLIO_REVIEW_JOB_REQUEST_EXAMPLE,
    REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE,
    REPORT_JOB_HANDLE_RESPONSE_EXAMPLE,
    REPORT_JOB_LIST_RESPONSE_EXAMPLE,
    REPORT_JOB_REGENERATE_RESPONSE_EXAMPLE,
    REPORT_JOB_RERENDER_RESPONSE_EXAMPLE,
    REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE,
    REPORT_JOB_STATUS_RESPONSE_EXAMPLE,
    ApiErrorResponse,
    PortfolioReviewJobRequest,
    ReportJobArchiveInfo,
    ReportJobDiagnosticsResponse,
    ReportJobHandleResponse,
    ReportJobLedgerRecord,
    ReportJobLineageDiagnostics,
    ReportJobListFilters,
    ReportJobListItem,
    ReportJobListResponse,
    ReportJobOperationLinks,
    ReportJobRegenerateRequest,
    ReportJobRegenerateResponse,
    ReportJobRenderInfo,
    ReportJobRerenderRequest,
    ReportJobRerenderResponse,
    ReportJobSnapshotDiagnostics,
    ReportJobStatusEventsResponse,
    ReportJobStatusResponse,
    ReportRerenderAttemptRecord,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.models import (
    ReportInputSnapshotRecord,
    ReportSnapshotLineageResponse,
    ReportUpstreamCallRecord,
)
from app.reporting_lineage.service import (
    get_portfolio_review_snapshot_capture_service,
    get_report_input_snapshot_store,
)
from app.reporting_lineage.store import ReportInputSnapshotNotFoundError
from app.reporting_metrics import record_report_operation
from app.reporting_render.regenerate_service import (
    ReportRegenerateResult,
    get_portfolio_review_regenerate_service,
)
from app.reporting_render.rerender_service import get_portfolio_review_rerender_service
from app.reporting_render.service import get_portfolio_review_render_orchestration_service
from app.routers.caller_context import caller_context_from_headers

router = APIRouter(prefix="/reports", tags=["Reports"])
jobs_router = APIRouter(prefix="/reports/jobs", tags=["Report Jobs"])
evidence_router = APIRouter(prefix="/reports", tags=["Report Evidence"])


class ReportLineageStore(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> ReportInputSnapshotRecord: ...

    def get_snapshot(self, snapshot_id: str) -> ReportInputSnapshotRecord: ...

    def list_upstream_calls(self, snapshot_id: str) -> list[ReportUpstreamCallRecord]: ...


def get_report_lineage_store() -> ReportLineageStore:
    return get_report_input_snapshot_store()


REPORT_JOB_SNAPSHOT_RESPONSE_EXAMPLE: dict[str, Any] = {
    "snapshot_id": "rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf",
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "report_type": "portfolio_review",
    "report_data_contract_version": "v1",
    "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
    "as_of_date": "2026-04-22",
    "snapshot_payload": {
        "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "as_of_date": "2026-04-22",
    },
    "snapshot_hash": "sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd",
    "snapshot_storage_ref": None,
    "supportability_status": "complete",
    "completeness_status": "complete",
    "lineage_summary": {
        "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
        "call_count": 8,
        "supportability_status": "complete",
        "partial_call_count": 0,
        "unavailable_call_count": 0,
        "not_supported_call_count": 0,
        "redacted_call_count": 0,
    },
    "captured_at": "2026-04-22T09:00:03Z",
    "created_at": "2026-04-22T09:00:03Z",
    "correlation_id": "corr-portfolio-review-1",
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
}

REPORT_JOB_LINEAGE_RESPONSE_EXAMPLE: dict[str, Any] = {
    "snapshot": REPORT_JOB_SNAPSHOT_RESPONSE_EXAMPLE,
    "upstream_calls": [
        {
            "upstream_call_id": "ruc_7c5d4f1e4cb6455fa11c06821c57b88f",
            "snapshot_id": "rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf",
            "service_name": "lotus-core",
            "endpoint": "/reporting/portfolio-summary/query",
            "method": "POST",
            "contract_version": "v1",
            "request_hash": (
                "sha256:0f5de8ef5cf305bf2e38ed33139e1df8f06fdf531f80903c123c25f6d8c09780"
            ),
            "response_hash": (
                "sha256:9de9c193650baf615ff8dca094d10ff18bdaabf0915963c4b3d74a3a07844f52"
            ),
            "response_ref": None,
            "status_code": 200,
            "latency_ms": 184,
            "supportability_status": "complete",
            "completeness_status": "complete",
            "failure_category": "none",
            "failure_message": None,
            "captured_at": "2026-04-22T09:00:02Z",
            "created_at": "2026-04-22T09:00:02Z",
            "correlation_id": "corr-portfolio-review-1",
            "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        }
    ],
}


def _error_response(
    status_code: int,
    *,
    example_key: str,
    description: str,
) -> dict[int, dict[str, Any]]:
    return {
        status_code: {
            "model": ApiErrorResponse,
            "description": description,
            "content": {
                "application/json": {
                    "example": API_ERROR_RESPONSE_EXAMPLES[example_key],
                }
            },
        }
    }


def _record_to_handle(record: ReportJobLedgerRecord) -> ReportJobHandleResponse:
    return ReportJobHandleResponse(
        report_request_id=record.request_id,
        report_job_id=record.job_id,
        status=record.status,
        status_url=f"/reports/jobs/{record.job_id}",
        idempotency_key=record.idempotency_key,
    )


def _record_to_render(record: ReportJobLedgerRecord) -> ReportJobRenderInfo | None:
    if (
        record.render_job_id is None
        and record.render_artifact_sha256 is None
        and record.render_output_format is None
    ):
        return None
    return ReportJobRenderInfo(
        render_job_id=record.render_job_id,
        output_format=record.render_output_format,
        template_id=record.render_template_id,
        template_version=record.render_template_version,
        artifact_sha256=record.render_artifact_sha256,
        bounded_determinism_fingerprint=record.render_bounded_determinism_fingerprint,
        runtime_engine=record.render_runtime_engine,
        runtime_engine_version=record.render_runtime_engine_version,
        render_duration_ms=record.render_duration_ms,
    )


def _record_to_archive(record: ReportJobLedgerRecord) -> ReportJobArchiveInfo | None:
    if (
        record.archive_request_id is None
        and record.archive_document_id is None
        and record.archive_completed_at is None
    ):
        return None
    return ReportJobArchiveInfo(
        archive_request_id=record.archive_request_id,
        document_id=record.archive_document_id,
        completed_at=record.archive_completed_at,
    )


def _attempt_to_rerender_response(
    attempt: ReportRerenderAttemptRecord,
) -> ReportJobRerenderResponse:
    return ReportJobRerenderResponse(
        report_job_id=attempt.report_job_id,
        rerender_attempt_id=attempt.rerender_attempt_id,
        idempotency_key=attempt.idempotency_key,
        status=attempt.status,
        snapshot_id=attempt.snapshot_id,
        snapshot_hash=attempt.snapshot_hash,
        previous_render_job_id=attempt.previous_render_job_id,
        previous_archive_document_id=attempt.previous_archive_document_id,
        failure_category=attempt.failure_category,
        failure_message=attempt.failure_message,
        retry_eligible=attempt.retry_eligible,
        render=ReportJobRenderInfo(
            render_job_id=attempt.render_job_id,
            output_format=attempt.render_output_format,
            template_id=attempt.render_template_id,
            template_version=attempt.render_template_version,
            artifact_sha256=attempt.render_artifact_sha256,
            bounded_determinism_fingerprint=attempt.render_bounded_determinism_fingerprint,
            runtime_engine=attempt.render_runtime_engine,
            runtime_engine_version=attempt.render_runtime_engine_version,
            render_duration_ms=attempt.render_duration_ms,
        ),
        archive=(
            ReportJobArchiveInfo(
                archive_request_id=attempt.archive_request_id,
                document_id=attempt.archive_document_id,
                completed_at=attempt.archive_completed_at,
            )
            if attempt.archive_request_id
            or attempt.archive_document_id
            or attempt.archive_completed_at
            else None
        ),
        created_at=attempt.created_at,
        updated_at=attempt.updated_at,
    )


def _regenerate_to_response(result: ReportRegenerateResult) -> ReportJobRegenerateResponse:
    regenerated = result.regenerated_job
    return ReportJobRegenerateResponse(
        source_report_job_id=result.source_job.job_id,
        regenerated_report_job_id=regenerated.job_id,
        idempotency_key=result.idempotency_key,
        status=regenerated.status,
        previous_snapshot_id=(
            result.previous_snapshot.snapshot_id if result.previous_snapshot else None
        ),
        new_snapshot_id=result.new_snapshot.snapshot_id if result.new_snapshot else None,
        previous_snapshot_hash=(
            result.previous_snapshot.snapshot_hash if result.previous_snapshot else None
        ),
        new_snapshot_hash=result.new_snapshot.snapshot_hash if result.new_snapshot else None,
        previous_archive_document_id=result.source_job.archive_document_id,
        new_archive_document_id=regenerated.archive_document_id,
        failure_category=regenerated.failure_category,
        failure_message=regenerated.failure_message,
        retry_eligible=regenerated.retry_eligible,
        render=_record_to_render(regenerated),
        archive=_record_to_archive(regenerated),
        created_at=regenerated.created_at,
        updated_at=regenerated.updated_at,
    )


def _record_to_status(record: ReportJobLedgerRecord) -> ReportJobStatusResponse:
    return ReportJobStatusResponse(
        report_job_id=record.job_id,
        report_request_id=record.request_id,
        report_type=record.report_type,
        portfolio_scope=record.portfolio_scope,
        status=record.status,
        failure_category=record.failure_category,
        failure_message=record.failure_message,
        current_step=record.current_step,
        retry_eligible=record.retry_eligible,
        cancel_requested=record.cancel_requested,
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        cancelled_at=record.cancelled_at,
        correlation_id=record.correlation_id,
        trace_id=record.trace_id,
        render=_record_to_render(record),
        archive=_record_to_archive(record),
    )


def _record_to_list_item(record: ReportJobLedgerRecord) -> ReportJobListItem:
    return ReportJobListItem(
        report_job_id=record.job_id,
        report_request_id=record.request_id,
        report_type=record.report_type,
        tenant_id=record.tenant_id,
        region=record.region,
        portfolio_scope=record.portfolio_scope,
        as_of_date=record.as_of_date,
        status=record.status,
        failure_category=record.failure_category,
        current_step=record.current_step,
        retry_eligible=record.retry_eligible,
        cancel_requested=record.cancel_requested,
        idempotency_key=record.idempotency_key,
        correlation_id=record.correlation_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        render=_record_to_render(record),
        archive=_record_to_archive(record),
    )


def _snapshot_to_diagnostics(
    snapshot: ReportInputSnapshotRecord,
) -> ReportJobSnapshotDiagnostics:
    return ReportJobSnapshotDiagnostics(
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        supportability_status=snapshot.supportability_status,
        completeness_status=snapshot.completeness_status,
        captured_at=snapshot.captured_at,
    )


def _lineage_to_diagnostics(
    snapshot: ReportInputSnapshotRecord,
    upstream_calls: list[ReportUpstreamCallRecord],
) -> ReportJobLineageDiagnostics:
    source_services = sorted({call.service_name for call in upstream_calls if call.service_name})
    failure_categories = sorted(
        {
            call.failure_category
            for call in upstream_calls
            if call.failure_category and call.failure_category != "none"
        }
    )
    return ReportJobLineageDiagnostics(
        upstream_call_count=len(upstream_calls),
        source_services=source_services,
        supportability_status=snapshot.supportability_status,
        completeness_status=snapshot.completeness_status,
        failure_categories=failure_categories,
    )


def _diagnostic_links(
    job_id: str, snapshot: ReportInputSnapshotRecord | None
) -> ReportJobOperationLinks:
    return ReportJobOperationLinks(
        status_url=f"/reports/jobs/{job_id}",
        events_url=f"/reports/jobs/{job_id}/events",
        snapshot_url=f"/reports/jobs/{job_id}/snapshot" if snapshot else None,
        lineage_url=f"/reports/jobs/{job_id}/lineage" if snapshot else None,
    )


@router.post(
    "/portfolio-reviews",
    response_model=ReportJobHandleResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit portfolio review report job",
    description=(
        "Creates a durable portfolio-review report job and returns its job handle. Use this "
        "endpoint when a caller wants asynchronous report orchestration with idempotent request "
        "identity. The endpoint persists the request/job/event ledger, captures the immutable "
        "report snapshot and upstream lineage, and when `pdf` is requested submits a governed "
        "render package to lotus-render before handing successful render artifacts to "
        "lotus-archive. Retrieval, retention execution, legal hold, purge, and distribution remain "
        "owned by lotus-archive."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": PORTFOLIO_REVIEW_JOB_REQUEST_EXAMPLE,
                    "examples": {
                        "portfolio_review_job": {
                            "summary": "Portfolio review job request",
                            "value": PORTFOLIO_REVIEW_JOB_REQUEST_EXAMPLE,
                        }
                    },
                }
            }
        },
        "responses": {
            "202": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_HANDLE_RESPONSE_EXAMPLE,
                        "examples": {
                            "accepted_job": {
                                "summary": "Accepted report job",
                                "value": REPORT_JOB_HANDLE_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        },
    },
    responses={
        **_error_response(
            400,
            example_key="missing_idempotency_key",
            description=(
                "Returned when the caller omits Idempotency-Key or required caller-context headers."
            ),
        ),
        **_error_response(
            409,
            example_key="idempotency_conflict",
            description=(
                "Returned when the supplied Idempotency-Key conflicts with a different request."
            ),
        ),
    },
)
async def submit_portfolio_review_job(
    request: PortfolioReviewJobRequest,
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    capture_service: Any = Depends(get_portfolio_review_snapshot_capture_service),
    render_service: Any = Depends(get_portfolio_review_render_orchestration_service),
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Required caller idempotency key for job creation.",
        ),
    ] = None,
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
    booking_center_code: Annotated[
        str | None,
        Header(alias="X-Booking-Center-Code", description="Optional booking center code."),
    ] = None,
    role: Annotated[
        str | None,
        Header(alias="X-Role", description="Optional caller role for audit diagnostics."),
    ] = None,
    correlation_id: Annotated[
        str | None,
        Header(alias="X-Correlation-ID", description="End-to-end correlation identifier."),
    ] = None,
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID", description="Distributed trace identifier."),
    ] = None,
) -> ReportJobHandleResponse:
    started_at = perf_counter()
    if not idempotency_key or not idempotency_key.strip():
        record_report_operation(
            operation="report_job_submission",
            status="failed",
            failure_category="missing_idempotency_key",
            duration_seconds=perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "missing_idempotency_key", "message": "Idempotency-Key is required."},
        )
    try:
        record = ledger.create_portfolio_review_job(
            request=request,
            caller_context=caller_context_from_headers(
                triggered_by=actor_id,
                caller_application=caller_application,
                tenant_id=tenant_id,
                region=region,
                booking_center_code=booking_center_code,
                role=role,
                correlation_id=correlation_id,
                trace_id=trace_id,
            ),
            idempotency_key=idempotency_key,
        )
    except MissingIdempotencyKeyError as exc:
        record_report_operation(
            operation="report_job_submission",
            status="failed",
            failure_category="missing_idempotency_key",
            duration_seconds=perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "missing_idempotency_key", "message": "Idempotency-Key is required."},
        ) from exc
    except IdempotencyConflictError as exc:
        record_report_operation(
            operation="report_job_submission",
            status="failed",
            failure_category="idempotency_conflict",
            duration_seconds=perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_conflict",
                "message": "Idempotency-Key was reused with a different report request.",
            },
        ) from exc
    if record.status == "accepted":
        record = await capture_service.capture_for_job(record)
    if record.status == "data_ready" and "pdf" in request.requested_output_formats:
        record = await render_service.render_for_job(record)
    record_report_operation(
        operation="report_job_submission",
        status=record.status,
        failure_category=record.failure_category,
        duration_seconds=perf_counter() - started_at,
    )
    return _record_to_handle(record)


@jobs_router.get(
    "",
    response_model=ReportJobListResponse,
    summary="Search report jobs for operations and support",
    description=(
        "Returns a bounded, support-safe list of report jobs that match the supplied filters. "
        "Use this endpoint when operations teams need to find jobs by tenant, region, status, "
        "portfolio, as-of date, idempotency key, or correlation identifier before drilling into "
        "one job or its append-only event history."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_LIST_RESPONSE_EXAMPLE,
                        "examples": {
                            "report_job_search": {
                                "summary": "Operational report-job search result",
                                "value": REPORT_JOB_LIST_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            400,
            example_key="invalid_report_job_filters",
            description="Returned when no supported search filter is supplied.",
        ),
    },
)
async def list_report_jobs(
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    tenant_filter: Annotated[
        str | None,
        Query(alias="tenantId", description="Return only jobs for this tenant identifier."),
    ] = None,
    region_filter: Annotated[
        str | None,
        Query(alias="region", description="Return only jobs for this operating region."),
    ] = None,
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Return only jobs in this current lifecycle status."),
    ] = None,
    report_type_filter: Annotated[
        str | None,
        Query(alias="reportType", description="Return only jobs for this report type."),
    ] = None,
    portfolio_id_filter: Annotated[
        str | None,
        Query(
            alias="portfolioId",
            description="Return only jobs whose scope includes this portfolio.",
        ),
    ] = None,
    as_of_date_filter: Annotated[
        str | None,
        Query(alias="asOfDate", description="Return only jobs for this business as-of date."),
    ] = None,
    idempotency_key_filter: Annotated[
        str | None,
        Query(alias="idempotencyKey", description="Return only jobs for this idempotency key."),
    ] = None,
    correlation_id_filter: Annotated[
        str | None,
        Query(
            alias="correlationId",
            description="Return only jobs for this correlation identifier.",
        ),
    ] = None,
    created_from: Annotated[
        str | None,
        Query(alias="createdFrom", description="Inclusive UTC lower bound for job creation time."),
    ] = None,
    created_to: Annotated[
        str | None,
        Query(alias="createdTo", description="Inclusive UTC upper bound for job creation time."),
    ] = None,
    limit: Annotated[
        int,
        Query(
            alias="limit",
            ge=1,
            le=100,
            description="Maximum number of report jobs returned by this bounded search.",
        ),
    ] = 25,
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
) -> ReportJobListResponse:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=None,
        trace_id=None,
    )
    filters = ReportJobListFilters.model_validate(
        {
            "tenant_id": tenant_filter,
            "region": region_filter,
            "status": status_filter,
            "report_type": report_type_filter,
            "portfolio_id": portfolio_id_filter,
            "as_of_date": as_of_date_filter,
            "idempotency_key": idempotency_key_filter,
            "correlation_id": correlation_id_filter,
            "created_from": created_from,
            "created_to": created_to,
            "limit": limit,
        }
    )
    if not any(
        [
            filters.tenant_id,
            filters.region,
            filters.status,
            filters.report_type,
            filters.portfolio_id,
            filters.as_of_date,
            filters.idempotency_key,
            filters.correlation_id,
            filters.created_from,
            filters.created_to,
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_report_job_filters",
                "message": "At least one supported job-search filter is required.",
            },
        )
    records = ledger.list_jobs(filters=filters)
    return ReportJobListResponse(
        count=len(records),
        applied_filters=filters,
        items=[_record_to_list_item(record) for record in records],
    )


@jobs_router.get(
    "/{job_id}",
    response_model=ReportJobStatusResponse,
    summary="Get report job status",
    description=(
        "Returns product-safe status and diagnostics for one report job. Use this endpoint after "
        "job submission or operational search when the caller needs the current lifecycle state "
        "and support-safe failure posture for a specific job."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_STATUS_RESPONSE_EXAMPLE,
                        "examples": {
                            "accepted_job_status": {
                                "summary": "Accepted report job status",
                                "value": REPORT_JOB_STATUS_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_job_not_found",
            description="Returned when the requested report job identifier does not exist.",
        ),
    },
)
async def get_report_job_status(
    job_id: Annotated[str, Path(description="Opaque report job identifier.")],
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
) -> ReportJobStatusResponse:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=None,
        trace_id=None,
    )
    try:
        return _record_to_status(ledger.get_job(job_id))
    except ReportJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc


@jobs_router.get(
    "/{job_id}/diagnostics",
    response_model=ReportJobDiagnosticsResponse,
    summary="Get report job operator diagnostics",
    description=(
        "Returns one composed, source-backed diagnostics view for a report job. Use this endpoint "
        "when support needs to inspect status, lifecycle events, snapshot posture, upstream "
        "lineage summary, render identifiers, and archive handoff identifiers without exposing "
        "raw report payloads, storage locations, database internals, or later replay commands."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE,
                        "examples": {
                            "report_job_diagnostics": {
                                "summary": "Report job operator diagnostics",
                                "value": REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_job_not_found",
            description="Returned when the requested report job identifier does not exist.",
        ),
        **_error_response(
            503,
            example_key="report_lineage_store_unavailable",
            description="Returned when source-backed lineage diagnostics cannot be queried.",
        ),
    },
)
async def get_report_job_diagnostics(
    job_id: Annotated[str, Path(description="Opaque report job identifier.")],
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    store: ReportLineageStore = Depends(get_report_lineage_store),
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
) -> ReportJobDiagnosticsResponse:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=None,
        trace_id=None,
    )
    try:
        record = ledger.get_job(job_id)
    except ReportJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc

    status_response = _record_to_status(record)
    events = ledger.list_status_events(job_id)
    snapshot: ReportInputSnapshotRecord | None = None
    upstream_calls: list[ReportUpstreamCallRecord] = []
    diagnostic_flags: list[str] = []
    try:
        snapshot = store.get_snapshot_by_job(job_id)
        upstream_calls = store.list_upstream_calls(snapshot.snapshot_id)
    except ReportInputSnapshotNotFoundError:
        diagnostic_flags.append("snapshot_not_captured")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=API_ERROR_RESPONSE_EXAMPLES["report_lineage_store_unavailable"]["detail"],
        ) from exc

    if record.status == "failed":
        diagnostic_flags.append("job_failed")
    if record.retry_eligible:
        diagnostic_flags.append("retry_eligible")
    if record.render_job_id and not record.archive_document_id:
        diagnostic_flags.append("archive_not_completed")

    return ReportJobDiagnosticsResponse(
        report_job_id=record.job_id,
        status=status_response,
        event_count=len(events),
        latest_event=events[-1] if events else None,
        snapshot=_snapshot_to_diagnostics(snapshot) if snapshot else None,
        lineage=_lineage_to_diagnostics(snapshot, upstream_calls) if snapshot else None,
        render=status_response.render,
        archive=status_response.archive,
        diagnostic_flags=diagnostic_flags,
        operation_links=_diagnostic_links(record.job_id, snapshot),
    )


@jobs_router.get(
    "/{job_id}/events",
    response_model=ReportJobStatusEventsResponse,
    summary="Get report job event history",
    description=(
        "Returns append-only lifecycle events for operational support and audit diagnostics. "
        "Use this endpoint when job status alone is insufficient to understand when a report job "
        "was accepted, transitioned, cancelled, or failed."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE,
                        "examples": {
                            "report_job_events": {
                                "summary": "Report job lifecycle events",
                                "value": REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_job_not_found",
            description="Returned when the requested report job identifier does not exist.",
        ),
    },
)
async def get_report_job_events(
    job_id: Annotated[str, Path(description="Opaque report job identifier.")],
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
) -> ReportJobStatusEventsResponse:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=None,
        trace_id=None,
    )
    try:
        ledger.get_job(job_id)
        return ReportJobStatusEventsResponse(
            report_job_id=job_id,
            events=ledger.list_status_events(job_id),
        )
    except ReportJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc


@jobs_router.post(
    "/{job_id}/rerender",
    response_model=ReportJobRerenderResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Rerender archived report job from immutable snapshot",
    description=(
        "Rerenders an already archived PDF report from the durable input snapshot captured for "
        "the source job. This command does not recollect upstream domain data. It preserves the "
        "source snapshot id and snapshot hash, creates a new render attempt identity, and records "
        "the archive correction consequence when a new document is handed off to lotus-archive."
    ),
    openapi_extra={
        "responses": {
            "202": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_RERENDER_RESPONSE_EXAMPLE,
                        "examples": {
                            "report_job_rerender": {
                                "summary": "Archived report rerendered from snapshot",
                                "value": REPORT_JOB_RERENDER_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            400,
            example_key="missing_idempotency_key",
            description="Returned when the rerender command omits Idempotency-Key.",
        ),
        **_error_response(
            404,
            example_key="report_job_not_found",
            description="Returned when the requested report job or snapshot does not exist.",
        ),
        **_error_response(
            409,
            example_key="report_job_cannot_be_rerendered",
            description="Returned when the report job is not archived PDF output.",
        ),
    },
)
async def rerender_report_job(
    job_id: Annotated[str, Path(description="Opaque archived report job identifier.")],
    command: ReportJobRerenderRequest,
    rerender_service: Any = Depends(get_portfolio_review_rerender_service),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Idempotency key for this rerender command."),
    ] = None,
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
    booking_center_code: Annotated[str | None, Header(alias="X-Booking-Center-Code")] = None,
    role: Annotated[str | None, Header(alias="X-Role")] = None,
    correlation_id: Annotated[
        str | None,
        Header(alias="X-Correlation-ID", description="End-to-end correlation identifier."),
    ] = None,
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID", description="Distributed trace identifier."),
    ] = None,
) -> ReportJobRerenderResponse:
    caller_context = caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=booking_center_code,
        role=role,
        correlation_id=correlation_id,
        trace_id=trace_id,
    )
    try:
        return _attempt_to_rerender_response(
            await rerender_service.rerender_job(
                job_id=job_id,
                command=command,
                caller_context=caller_context,
                idempotency_key=idempotency_key,
            )
        )
    except MissingIdempotencyKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=API_ERROR_RESPONSE_EXAMPLES["missing_idempotency_key"]["detail"],
        ) from exc
    except ReportJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc
    except InvalidReportJobTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=API_ERROR_RESPONSE_EXAMPLES["report_job_cannot_be_rerendered"]["detail"],
        ) from exc


@jobs_router.post(
    "/{job_id}/regenerate",
    response_model=ReportJobRegenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Regenerate archived report job from upstream data",
    description=(
        "Creates a new report job from an archived PDF source job, recollects upstream domain "
        "data into a fresh snapshot and lineage bundle, renders a new document, and archives it "
        "as a replacement for the previous archived document. Use regenerate when source data has "
        "changed or needs correction; use rerender when only presentation needs correction."
    ),
    openapi_extra={
        "responses": {
            "202": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_REGENERATE_RESPONSE_EXAMPLE,
                        "examples": {
                            "report_job_regenerate": {
                                "summary": "Archived report regenerated from upstream data",
                                "value": REPORT_JOB_REGENERATE_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            400,
            example_key="missing_idempotency_key",
            description="Returned when the regenerate command omits Idempotency-Key.",
        ),
        **_error_response(
            404,
            example_key="report_job_not_found",
            description="Returned when the requested report job does not exist.",
        ),
        **_error_response(
            409,
            example_key="report_job_cannot_be_regenerated",
            description="Returned when the report job is not archived PDF output.",
        ),
    },
)
async def regenerate_report_job(
    job_id: Annotated[str, Path(description="Opaque archived report job identifier.")],
    command: ReportJobRegenerateRequest,
    regenerate_service: Any = Depends(get_portfolio_review_regenerate_service),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Idempotency key for this regenerate command."),
    ] = None,
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
    booking_center_code: Annotated[str | None, Header(alias="X-Booking-Center-Code")] = None,
    role: Annotated[str | None, Header(alias="X-Role")] = None,
    correlation_id: Annotated[
        str | None,
        Header(alias="X-Correlation-ID", description="End-to-end correlation identifier."),
    ] = None,
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID", description="Distributed trace identifier."),
    ] = None,
) -> ReportJobRegenerateResponse:
    caller_context = caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=booking_center_code,
        role=role,
        correlation_id=correlation_id,
        trace_id=trace_id,
    )
    try:
        return _regenerate_to_response(
            await regenerate_service.regenerate_job(
                job_id=job_id,
                command=command,
                caller_context=caller_context,
                idempotency_key=idempotency_key,
            )
        )
    except MissingIdempotencyKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=API_ERROR_RESPONSE_EXAMPLES["missing_idempotency_key"]["detail"],
        ) from exc
    except ReportJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc
    except InvalidReportJobTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=API_ERROR_RESPONSE_EXAMPLES["report_job_cannot_be_regenerated"]["detail"],
        ) from exc


@jobs_router.post(
    "/{job_id}/cancel",
    response_model=ReportJobStatusResponse,
    summary="Cancel report job before render or archive",
    description=(
        "Cancels a report job only while it is still before render, archive, or completion "
        "phases. Use this endpoint only when an accepted or in-flight pre-render job must be "
        "stopped. Render and archive handoff outcomes are recorded in the job ledger; retrieval, "
        "retention execution, legal hold, purge, rerender, and reissue semantics are outside this "
        "endpoint."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            **REPORT_JOB_STATUS_RESPONSE_EXAMPLE,
                            "status": "cancelled",
                            "failure_category": "cancelled",
                            "failure_message": (
                                "Report job cancelled before render or archive processing."
                            ),
                            "current_step": "cancelled",
                            "cancel_requested": True,
                            "cancelled_at": "2026-04-22T09:01:00Z",
                        }
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_job_not_found",
            description="Returned when the requested report job identifier does not exist.",
        ),
        **_error_response(
            409,
            example_key="report_job_cannot_be_cancelled",
            description="Returned when the job has already completed or was already cancelled.",
        ),
    },
)
async def cancel_report_job(
    job_id: Annotated[str, Path(description="Opaque report job identifier.")],
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
    correlation_id: Annotated[
        str | None,
        Header(alias="X-Correlation-ID", description="End-to-end correlation identifier."),
    ] = None,
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID", description="Distributed trace identifier."),
    ] = None,
) -> ReportJobStatusResponse:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=correlation_id,
        trace_id=trace_id,
    )
    try:
        return _record_to_status(
            ledger.cancel_job(
                job_id=job_id,
                actor=actor_id or "unknown",
                correlation_id=correlation_id or "",
                trace_id=trace_id or "",
            )
        )
    except ReportJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc
    except InvalidReportJobTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "report_job_cannot_be_cancelled",
                "message": "Report job can no longer be cancelled.",
            },
        ) from exc


@evidence_router.get(
    "/jobs/{job_id}/snapshot",
    response_model=ReportInputSnapshotRecord,
    summary="Get durable report input snapshot by job",
    description=(
        "Returns the durable report input snapshot that belongs to one report job. Use this "
        "endpoint when support or audit needs the exact support-safe machine-readable input state "
        "captured for that job and business date."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_SNAPSHOT_RESPONSE_EXAMPLE,
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_job_not_found",
            description="Returned when no durable snapshot exists for the requested job.",
        ),
    },
)
async def get_report_job_snapshot(
    job_id: Annotated[str, Path(description="Opaque report job identifier.")],
    store: ReportLineageStore = Depends(get_report_lineage_store),
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    caller_application: Annotated[str | None, Header(alias="X-Caller-Application")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    region: Annotated[str | None, Header(alias="X-Region")] = None,
) -> ReportInputSnapshotRecord:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=None,
        trace_id=None,
    )
    try:
        return store.get_snapshot_by_job(job_id)
    except ReportInputSnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc


@evidence_router.get(
    "/jobs/{job_id}/lineage",
    response_model=ReportSnapshotLineageResponse,
    summary="Get durable upstream lineage by job",
    description=(
        "Returns the durable upstream-call lineage associated with one report job. Use this "
        "endpoint when support or audit needs to see which authoritative Lotus services, "
        "endpoints, hashes, and supportability postures were recorded for that job."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_LINEAGE_RESPONSE_EXAMPLE,
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_job_not_found",
            description="Returned when no durable lineage exists for the requested job.",
        ),
    },
)
async def get_report_job_lineage(
    job_id: Annotated[str, Path(description="Opaque report job identifier.")],
    store: ReportLineageStore = Depends(get_report_lineage_store),
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    caller_application: Annotated[str | None, Header(alias="X-Caller-Application")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    region: Annotated[str | None, Header(alias="X-Region")] = None,
) -> ReportSnapshotLineageResponse:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=None,
        trace_id=None,
    )
    try:
        snapshot = store.get_snapshot_by_job(job_id)
    except ReportInputSnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc
    return ReportSnapshotLineageResponse(
        snapshot=snapshot,
        upstream_calls=store.list_upstream_calls(snapshot.snapshot_id),
    )


@evidence_router.get(
    "/snapshots/{snapshot_id}",
    response_model=ReportInputSnapshotRecord,
    summary="Get durable report input snapshot by snapshot id",
    description=(
        "Returns one durable report input snapshot by its snapshot identifier. Use this endpoint "
        "when an operator already has the snapshot identifier from evidence review or a prior "
        "lineage lookup and needs the same support-safe snapshot contract directly."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_SNAPSHOT_RESPONSE_EXAMPLE,
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_snapshot_not_found",
            description="Returned when the requested snapshot identifier does not exist.",
        ),
    },
)
async def get_snapshot(
    snapshot_id: Annotated[str, Path(description="Opaque durable snapshot identifier.")],
    store: ReportLineageStore = Depends(get_report_lineage_store),
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    caller_application: Annotated[str | None, Header(alias="X-Caller-Application")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    region: Annotated[str | None, Header(alias="X-Region")] = None,
) -> ReportInputSnapshotRecord:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=None,
        trace_id=None,
    )
    try:
        return store.get_snapshot(snapshot_id)
    except ReportInputSnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "report_snapshot_not_found",
                "message": "Report snapshot was not found.",
            },
        ) from exc


@evidence_router.get(
    "/snapshots/{snapshot_id}/lineage",
    response_model=ReportSnapshotLineageResponse,
    summary="Get durable upstream lineage by snapshot id",
    description=(
        "Returns one durable report input snapshot together with its append-only upstream-call "
        "lineage. Use this endpoint when an operator needs the snapshot and the precise upstream "
        "evidence rows in the same certified response."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_JOB_LINEAGE_RESPONSE_EXAMPLE,
                    }
                }
            }
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_snapshot_not_found",
            description="Returned when the requested snapshot identifier does not exist.",
        ),
    },
)
async def get_snapshot_lineage(
    snapshot_id: Annotated[str, Path(description="Opaque durable snapshot identifier.")],
    store: ReportLineageStore = Depends(get_report_lineage_store),
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    caller_application: Annotated[str | None, Header(alias="X-Caller-Application")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    region: Annotated[str | None, Header(alias="X-Region")] = None,
) -> ReportSnapshotLineageResponse:
    caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=None,
        role=None,
        correlation_id=None,
        trace_id=None,
    )
    try:
        snapshot = store.get_snapshot(snapshot_id)
    except ReportInputSnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "report_snapshot_not_found",
                "message": "Report snapshot was not found.",
            },
        ) from exc
    return ReportSnapshotLineageResponse(
        snapshot=snapshot,
        upstream_calls=store.list_upstream_calls(snapshot_id),
    )
