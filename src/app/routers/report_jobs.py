from typing import Annotated, Any

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
    REPORT_JOB_HANDLE_RESPONSE_EXAMPLE,
    REPORT_JOB_LIST_RESPONSE_EXAMPLE,
    REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE,
    REPORT_JOB_STATUS_RESPONSE_EXAMPLE,
    ApiErrorResponse,
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobHandleResponse,
    ReportJobLedgerRecord,
    ReportJobListFilters,
    ReportJobListItem,
    ReportJobListResponse,
    ReportJobStatusEventsResponse,
    ReportJobStatusResponse,
)
from app.reporting_jobs.service import get_report_job_ledger

router = APIRouter(prefix="/reports", tags=["Reports"])
jobs_router = APIRouter(prefix="/reports/jobs", tags=["Report Jobs"])


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
    )


def _caller_context(
    *,
    triggered_by: str | None,
    caller_application: str | None,
    tenant_id: str | None,
    region: str | None,
    booking_center_code: str | None,
    role: str | None,
    correlation_id: str | None,
    trace_id: str | None,
) -> ReportCallerContext:
    missing = [
        name
        for name, value in {
            "X-Actor-Id": triggered_by,
            "X-Caller-Application": caller_application,
            "X-Tenant-Id": tenant_id,
            "X-Region": region,
        }.items()
        if not value or not value.strip()
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_caller_context",
                "message": "Required caller context headers are missing.",
                "missing_headers": missing,
            },
        )
    assert triggered_by is not None
    assert caller_application is not None
    assert tenant_id is not None
    assert region is not None
    return ReportCallerContext(
        triggered_by=triggered_by.strip(),
        caller_application=caller_application.strip(),
        tenant_id=tenant_id.strip(),
        region=region.strip(),
        booking_center_code=booking_center_code,
        role=role,
        correlation_id=correlation_id or "",
        trace_id=trace_id or "",
    )


@router.post(
    "/portfolio-reviews",
    response_model=ReportJobHandleResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit portfolio review report job",
    description=(
        "Creates a durable portfolio-review report job and returns its job handle. Use this "
        "endpoint when a caller wants asynchronous report orchestration with idempotent request "
        "identity. The endpoint records request, job, and lifecycle-event ledger entries only; "
        "it does not render PDF output or archive documents."
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
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "missing_idempotency_key", "message": "Idempotency-Key is required."},
        )
    try:
        record = ledger.create_portfolio_review_job(
            request=request,
            caller_context=_caller_context(
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "missing_idempotency_key", "message": "Idempotency-Key is required."},
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_conflict",
                "message": "Idempotency-Key was reused with a different report request.",
            },
        ) from exc
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
    _caller_context(
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
    _caller_context(
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
    _caller_context(
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
    "/{job_id}/cancel",
    response_model=ReportJobStatusResponse,
    summary="Cancel report job before render or archive",
    description=(
        "Cancels a report job only while it is still before render, archive, or completion "
        "phases. Use this endpoint only when an accepted or in-flight pre-render job must be "
        "stopped. Render, archive, rerender, and reissue semantics are owned by later reporting "
        "waves."
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
    _caller_context(
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
