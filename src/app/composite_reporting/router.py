"""Order pinned composite datasets through the existing durable job worker."""

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException

from app.composite_reporting.models import AmendmentEligibilitySelection, CompositeReviewJobRequest
from app.observability import correlation_id_var, trace_id_var
from app.report_ordering_catalogue.router import get_report_ordering_catalogue_service
from app.report_ordering_catalogue.service import ReportOrderingCatalogueService
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
        "Supported XLSX contracts render the retained dataset through Render and Archive. "
        "Monthly source-correction v6 is eligibility evidence: it supplies no "
        "TWR, MWR, dispersion, contribution or model-fee calculation. "
        "The calculated review is NOT_ATTESTED and restricted to internal control use."
    ),
    responses={
        400: {
            "description": "Missing caller context or idempotency key; mismatched source tenant."
        },
        409: {"description": "Idempotency key already identifies different report content."},
        503: {"description": "The selected contract has no supported XLSX delivery path."},
    },
)
async def submit_composite_review(
    request: CompositeReviewJobRequest,
    caller: Annotated[ReportCallerContext, Depends(caller_context_dependency)],
    ledger: Annotated[CompositeJobLedger, Depends(get_report_job_ledger)],
    catalogue: Annotated[
        ReportOrderingCatalogueService, Depends(get_report_ordering_catalogue_service)
    ],
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
        options=request.capture_options(),
    )
    if request.primary_selection.tenant_id != caller.tenant_id:
        raise HTTPException(
            400,
            detail={
                "code": "composite_report_tenant_mismatch",
                "message": "Pinned source scope does not match the caller.",
            },
        )
    profile = (
        "v5"
        if request.pooled_selection is not None
        else "v6"
        if isinstance(request.eligibility_selection, AmendmentEligibilitySelection)
        else "v4"
        if request.eligibility_selection is not None
        else ("v3" if request.linked_selection is not None else "v2")
    )
    if (
        request.source_products is not None
        or request.linked_selection is not None
        or request.eligibility_selection is not None
        or request.pooled_selection is not None
    ) and request.requested_output_formats == ["xlsx"]:
        support = await catalogue.document_contract_supportability(
            report_type="composite_review",
            format_id="xlsx",
            contract_version=f"composite_review.{profile}",
            template_version=profile,
        )
        if support.state != "ready":
            raise HTTPException(
                503,
                detail={
                    "code": "composite_product_render_unavailable",
                    "message": "The selected product contract has no compatible XLSX renderer.",
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
