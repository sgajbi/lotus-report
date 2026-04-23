from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status

from app.models.contracts import (
    PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE,
    PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE,
    PortfolioReviewReportRequest,
    PortfolioReviewReportResponse,
)
from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import (
    PORTFOLIO_REVIEW_JOB_REQUEST_EXAMPLE,
    REPORT_JOB_HANDLE_RESPONSE_EXAMPLE,
    REPORT_JOB_STATUS_RESPONSE_EXAMPLE,
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobHandleResponse,
    ReportJobLedgerRecord,
    ReportJobStatusResponse,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.services.reporting_read_service import ReportingReadService

router = APIRouter(prefix="/reports", tags=["Reports"])


def get_reporting_read_service() -> ReportingReadService:
    return ReportingReadService()


def _apply_requested_section_limit(payload: dict[str, Any], section_limit: int) -> dict[str, Any]:
    limited_payload = dict(payload)
    sections = limited_payload.get("sections")
    if isinstance(sections, list) and len(sections) > section_limit:
        limited_payload["sections"] = sections[:section_limit]
    return limited_payload


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
    return ReportCallerContext(
        triggered_by=triggered_by or "unknown",
        caller_application=caller_application or "unknown",
        tenant_id=tenant_id or "default",
        region=region or "UNSPECIFIED",
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
        "Accepts a portfolio review report job request and returns a durable job handle. "
        "This endpoint creates request/job/status ledger records only; it does not render PDF "
        "documents or archive outputs."
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
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    caller_application: Annotated[str | None, Header(alias="X-Caller-Application")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    region: Annotated[str | None, Header(alias="X-Region")] = None,
    booking_center_code: Annotated[str | None, Header(alias="X-Booking-Center-Code")] = None,
    role: Annotated[str | None, Header(alias="X-Role")] = None,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    trace_id: Annotated[str | None, Header(alias="X-Trace-ID")] = None,
) -> ReportJobHandleResponse:
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


@router.get(
    "/jobs/{job_id}",
    response_model=ReportJobStatusResponse,
    summary="Get report job status",
    description="Returns product-safe status and diagnostics for one report job.",
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
)
async def get_report_job_status(
    job_id: Annotated[str, Path(description="Opaque report job identifier.")],
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
) -> ReportJobStatusResponse:
    try:
        return _record_to_status(ledger.get_job(job_id))
    except ReportJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_job_not_found", "message": "Report job was not found."},
        ) from exc


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=ReportJobStatusResponse,
    summary="Cancel report job before render or archive",
    description=(
        "Cancels a report job only while it is still before render/archive/completion phases. "
        "Render, archive, rerender, and reissue semantics are owned by later reporting RFCs."
    ),
)
async def cancel_report_job(
    job_id: Annotated[str, Path(description="Opaque report job identifier.")],
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    trace_id: Annotated[str | None, Header(alias="X-Trace-ID")] = None,
) -> ReportJobStatusResponse:
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


@router.post(
    "/portfolios/{portfolio_id}/summary",
    response_model=dict[str, Any],
    summary="Get portfolio summary (lotus-report-owned)",
    description=(
        "lotus-report-owned reporting endpoint for consolidated portfolio summary. "
        "Phase-1 source is lotus-core upstream while ownership moves to lotus-report."
    ),
)
async def get_portfolio_summary(
    portfolio_id: Annotated[str, Path(description="Canonical portfolio identifier.")],
    request: dict[str, Any],
    section_limit: Annotated[int, Query(ge=1, le=20, description="pagination")] = 10,
    service: ReportingReadService = Depends(get_reporting_read_service),
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> dict[str, Any]:
    return await service.get_portfolio_summary(
        portfolio_id=portfolio_id,
        request_payload=_apply_requested_section_limit(request, section_limit),
        correlation_id=correlation_id,
    )


@router.post(
    "/portfolios/{portfolio_id}/review",
    response_model=PortfolioReviewReportResponse,
    summary="Get portfolio review report",
    description=(
        "Returns the lotus-report-owned portfolio review contract for front-office client/advisor "
        "review meetings. The response is machine-readable JSON with sourced client profile, key "
        "figures, client-ready sections, advisor-only sections, report coverage, observations, "
        "evidence lineage, deterministic advisor briefing, presentation structure, and guarded "
        "AI-readiness metadata. The endpoint composes authoritative lotus-core, lotus-performance, "
        "and lotus-risk data and marks unsupported suitability, mandate-control, "
        "target-allocation, tax-lot, realized-gain/loss, and advice features as not sourced "
        "rather than inventing report content."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE,
                    "examples": {
                        "full_portfolio_review": {
                            "summary": "Full portfolio review request",
                            "value": PORTFOLIO_REVIEW_FULL_REQUEST_EXAMPLE,
                        }
                    },
                }
            }
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE,
                        "examples": {
                            "full_portfolio_review": {
                                "summary": "Full portfolio review response",
                                "value": PORTFOLIO_REVIEW_FULL_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        },
    },
)
async def get_portfolio_review(
    portfolio_id: Annotated[
        str,
        Path(
            description=(
                "Canonical portfolio identifier. Use PB_SG_GLOBAL_BAL_001 for governed local "
                "front-office proof unless a different dataset is explicitly required."
            )
        ),
    ],
    request: PortfolioReviewReportRequest,
    section_limit: Annotated[
        int,
        Query(
            ge=1,
            le=20,
            description=(
                "Maximum number of requested section keys to honor from the request body. This "
                "is a guardrail, not result-row pagination."
            ),
        ),
    ] = 10,
    service: ReportingReadService = Depends(get_reporting_read_service),
    correlation_id: Annotated[
        str | None,
        Header(
            alias="X-Correlation-ID",
            description="Caller-supplied correlation id propagated to upstream service calls.",
        ),
    ] = None,
) -> dict[str, Any]:
    return await service.get_portfolio_review(
        portfolio_id=portfolio_id,
        request_payload=_apply_requested_section_limit(
            request.model_dump(exclude_none=True, mode="json"), section_limit
        ),
        correlation_id=correlation_id,
    )
