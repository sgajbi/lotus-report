"""Order pinned composite datasets through the existing durable job worker."""

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException

from app.composite_reporting.models import CompositeReviewJobRequest
from app.observability import correlation_id_var, trace_id_var
from app.reporting_jobs.ledger import IdempotencyConflictError, MissingIdempotencyKeyError
from app.reporting_jobs.models import (
    ReportCallerContext,
    ReportJobHandleResponse,
    ReportJobLedgerRecord,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.routers.caller_context import caller_context_dependency
from app.routers.report_jobs import _record_to_handle
from app.routers.report_ordering_validation import enforce_report_ordering_submission

router = APIRouter(prefix="/reports", tags=["Reports"])


class CompositeJobLedger(Protocol):
    def submit_composite_review_job(
        self,
        *,
        request: CompositeReviewJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportJobLedgerRecord: ...


@router.post(
    "/composite-reviews",
    status_code=202,
    response_model=ReportJobHandleResponse,
    summary="Order an exact retained composite calculated review",
    description=(
        "Queues one immutable calculated composite selection for the existing report worker. "
        "No latest selection, financial recalculation or official approval is inferred. "
        "The structured dataset retains exact provenance and explicit unavailable products. "
        "XLSX requests render the retained dataset through Render and Archive. "
        "The calculated review is NOT_ATTESTED and restricted to internal control use."
    ),
    responses={
        400: {
            "description": "Missing caller context or idempotency key; mismatched source tenant."
        },
        409: {"description": "Idempotency key already identifies different report content."},
    },
)
def submit_composite_review(
    request: CompositeReviewJobRequest,
    caller: Annotated[ReportCallerContext, Depends(caller_context_dependency)],
    ledger: Annotated[CompositeJobLedger, Depends(get_report_job_ledger)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Required immutable caller retry identity."),
    ] = None,
) -> ReportJobHandleResponse:
    # Persist the middleware-admitted request propagation identity when the
    # caller omitted optional transport headers. Never accept an unexecutable
    # durable job with empty correlation/trace while HTTP minted both.
    caller = caller.model_copy(
        update={
            "correlation_id": caller.correlation_id or correlation_id_var.get(),
            "trace_id": caller.trace_id or trace_id_var.get(),
        }
    )
    enforce_report_ordering_submission(
        report_family_id="composite_review",
        ordering_mode_id="single_composite",
        requested_output_formats=request.requested_output_formats,
        options={
            **request.options,
            "composite_selection": request.selection.model_dump(mode="json"),
        },
    )
    if request.selection.tenant_id != caller.tenant_id:
        raise HTTPException(
            400,
            detail={
                "code": "composite_report_tenant_mismatch",
                "message": "Pinned source scope does not match the caller.",
            },
        )
    try:
        record = ledger.submit_composite_review_job(
            request=request,
            caller_context=caller,
            idempotency_key=idempotency_key,
        )
    except MissingIdempotencyKeyError as exc:
        raise HTTPException(
            400,
            detail={"code": "missing_idempotency_key", "message": "Idempotency-Key is required."},
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "idempotency_conflict",
                "message": "The retry identity belongs to different report content.",
            },
        ) from exc
    return _record_to_handle(record)
