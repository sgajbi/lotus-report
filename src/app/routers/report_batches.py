from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status

from app.report_batch_orchestrator.ledger import (
    BatchIdempotencyConflictError,
    MissingBatchIdempotencyKeyError,
)
from app.report_batch_orchestrator.models import (
    BATCH_CONTROL_RESPONSE_EXAMPLE,
    BATCH_CREATE_REQUEST_EXAMPLE,
    BATCH_HANDLE_RESPONSE_EXAMPLE,
    BATCH_RECOVERY_RESPONSE_EXAMPLE,
    BATCH_STATUS_RESPONSE_EXAMPLE,
    BatchControlResponse,
    BatchCreateRequest,
    BatchHandleResponse,
    BatchItemStatusResponse,
    BatchRecoveryResponse,
    BatchRetryPolicy,
    BatchStatus,
    BatchStatusResponse,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.selector import BatchSelectorValidationError
from app.report_batch_orchestrator.service import get_report_batch_ledger
from app.reporting_jobs.models import ApiErrorResponse, ReportCallerContext
from app.routers.caller_context import caller_context_dependency

router = APIRouter(prefix="/reports/batches", tags=["Report Batches"])


class ReportBatchLedgerPort(Protocol):
    def create_batch(
        self,
        *,
        request: BatchCreateRequest,
        caller_context: Any,
        idempotency_key: str | None,
    ) -> ReportBatchRecord: ...

    def get_batch(self, batch_id: str) -> ReportBatchRecord: ...

    def pause_batch(self, *, batch_id: str) -> Any: ...

    def resume_batch(self, *, batch_id: str) -> Any: ...

    def cancel_batch(self, *, batch_id: str) -> Any: ...

    def retry_failed_items(
        self,
        *,
        batch_id: str,
        retry_policy: BatchRetryPolicy | None = None,
    ) -> Any: ...

    def recover_expired_leases(self, *, batch_id: str) -> Any: ...


BATCH_API_ERROR_RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
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
            "message": "Idempotency-Key was reused with a different batch request.",
        }
    },
    "invalid_batch_selector": {
        "detail": {
            "code": "invalid_batch_selector",
            "message": "Batch selector could not be materialized from eligible portfolios.",
        }
    },
    "report_batch_not_found": {
        "detail": {
            "code": "report_batch_not_found",
            "message": "Report batch was not found.",
        }
    },
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
                    "example": BATCH_API_ERROR_RESPONSE_EXAMPLES[example_key],
                }
            },
        }
    }


def _status_url(batch_id: str) -> str:
    return f"/reports/batches/{batch_id}"


def _record_to_handle(record: ReportBatchRecord) -> BatchHandleResponse:
    return BatchHandleResponse(
        batch_id=record.batch_id,
        status=record.status,
        status_url=_status_url(record.batch_id),
        idempotency_key=record.idempotency_key,
        item_count=record.item_count,
    )


def _record_to_status(record: ReportBatchRecord) -> BatchStatusResponse:
    status_counts: dict[str, int] = {}
    for item in record.items:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1
    return BatchStatusResponse(
        batch_id=record.batch_id,
        selector_mode=record.selector_mode,
        tenant_id=record.tenant_id,
        region=record.region,
        materialized_portfolio_ids=record.materialized_portfolio_ids,
        as_of_date=record.as_of_date,
        requested_output_formats=record.requested_output_formats,
        reporting_currency=record.reporting_currency,
        status=record.status,
        item_count=record.item_count,
        status_counts=status_counts,
        items=[
            BatchItemStatusResponse(
                batch_item_id=item.batch_item_id,
                item_position=item.item_position,
                portfolio_id=item.portfolio_id,
                status=item.status,
                report_job_id=item.report_job_id,
                attempt_count=item.attempt_count,
                retry_eligible=item.retry_eligible,
                next_retry_at=item.next_retry_at,
                last_error_category=item.last_error_category,
                last_error_summary=item.last_error_summary,
                created_at=item.created_at,
                started_at=item.started_at,
                completed_at=item.completed_at,
                cancelled_at=item.cancelled_at,
            )
            for item in record.items
        ],
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        cancelled_at=record.cancelled_at,
        failed_at=record.failed_at,
        correlation_id=record.correlation_id,
        trace_id=record.trace_id,
    )


def _control_response(
    batch_id: str, *, status_value: BatchStatus, affected_count: int
) -> BatchControlResponse:
    return BatchControlResponse(
        batch_id=batch_id,
        status=status_value,
        affected_count=affected_count,
        status_url=_status_url(batch_id),
    )


def _not_found_error(exc: ValueError) -> HTTPException:
    if str(exc) == "report_batch_not_found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=BATCH_API_ERROR_RESPONSE_EXAMPLES["report_batch_not_found"]["detail"],
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "batch_operation_failed", "message": str(exc)},
    )


@router.post(
    "",
    response_model=BatchHandleResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create report batch",
    description=(
        "Creates a durable report batch from eligible portfolio candidates and returns a batch "
        "handle. Use this endpoint when operations need a governed, idempotent batch container "
        "before item-level dispatch. This endpoint materializes batch items only; scheduled "
        "production loops and worker execution are separate capabilities."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": BATCH_CREATE_REQUEST_EXAMPLE,
                    "examples": {
                        "explicit_portfolio_list": {
                            "summary": "Explicit portfolio list",
                            "value": BATCH_CREATE_REQUEST_EXAMPLE,
                        }
                    },
                }
            }
        },
        "responses": {
            "202": {
                "content": {
                    "application/json": {
                        "example": BATCH_HANDLE_RESPONSE_EXAMPLE,
                        "examples": {
                            "materialized_batch": {
                                "summary": "Materialized report batch",
                                "value": BATCH_HANDLE_RESPONSE_EXAMPLE,
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
                "Returned when the caller omits Idempotency-Key, required caller-context headers, "
                "or a materializable selector."
            ),
        ),
        **_error_response(
            409,
            example_key="idempotency_conflict",
            description=(
                "Returned when the supplied Idempotency-Key conflicts with a different batch "
                "request."
            ),
        ),
    },
)
async def create_report_batch(
    request: BatchCreateRequest,
    ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    caller_context: ReportCallerContext = Depends(caller_context_dependency),
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Required caller idempotency key for batch creation.",
        ),
    ] = None,
) -> BatchHandleResponse:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=BATCH_API_ERROR_RESPONSE_EXAMPLES["missing_idempotency_key"]["detail"],
        )
    try:
        record = ledger.create_batch(
            request=request,
            caller_context=caller_context,
            idempotency_key=idempotency_key,
        )
    except MissingBatchIdempotencyKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=BATCH_API_ERROR_RESPONSE_EXAMPLES["missing_idempotency_key"]["detail"],
        ) from exc
    except BatchIdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=BATCH_API_ERROR_RESPONSE_EXAMPLES["idempotency_conflict"]["detail"],
        ) from exc
    except BatchSelectorValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return _record_to_handle(record)


@router.get(
    "/{batch_id}",
    response_model=BatchStatusResponse,
    summary="Get report batch status",
    description=(
        "Returns product-safe status for a durable report batch and its materialized items. Use "
        "this endpoint when operations need progress, item posture, retry eligibility, or linked "
        "report-job identifiers for a known batch."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": BATCH_STATUS_RESPONSE_EXAMPLE,
                        "examples": {
                            "batch_status": {
                                "summary": "Report batch status",
                                "value": BATCH_STATUS_RESPONSE_EXAMPLE,
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
            example_key="report_batch_not_found",
            description="Returned when the requested report batch does not exist.",
        )
    },
)
async def get_report_batch_status(
    batch_id: Annotated[
        str,
        Path(description="Opaque durable report batch identifier.", examples=["rbch_example"]),
    ],
    ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    _caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchStatusResponse:
    try:
        return _record_to_status(ledger.get_batch(batch_id))
    except ValueError as exc:
        raise _not_found_error(exc) from exc


@router.post(
    "/{batch_id}:pause",
    response_model=BatchControlResponse,
    summary="Pause report batch dispatch",
    description=(
        "Pauses a materialized or running report batch so no new items are dispatched. Use this "
        "endpoint for controlled operational intervention while already-created report jobs "
        "continue under their own job lifecycle."
    ),
    openapi_extra={
        "responses": {
            "200": {"content": {"application/json": {"example": BATCH_CONTROL_RESPONSE_EXAMPLE}}}
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_batch_not_found",
            description="Returned when the requested report batch does not exist.",
        )
    },
)
async def pause_report_batch(
    batch_id: Annotated[str, Path(description="Opaque durable report batch identifier.")],
    ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    _caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchControlResponse:
    try:
        result = ledger.pause_batch(batch_id=batch_id)
    except ValueError as exc:
        raise _not_found_error(exc) from exc
    return _control_response(
        batch_id=result.batch_id,
        status_value=result.batch_status,
        affected_count=result.affected_count,
    )


@router.post(
    "/{batch_id}:resume",
    response_model=BatchControlResponse,
    summary="Resume report batch dispatch",
    description=(
        "Resumes a paused report batch so eligible items may be dispatched by the batch execution "
        "capability once it is enabled. Use this endpoint only after the pause condition "
        "has been cleared."
    ),
    openapi_extra={
        "responses": {
            "200": {"content": {"application/json": {"example": BATCH_CONTROL_RESPONSE_EXAMPLE}}}
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_batch_not_found",
            description="Returned when the requested report batch does not exist.",
        )
    },
)
async def resume_report_batch(
    batch_id: Annotated[str, Path(description="Opaque durable report batch identifier.")],
    ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    _caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchControlResponse:
    try:
        result = ledger.resume_batch(batch_id=batch_id)
    except ValueError as exc:
        raise _not_found_error(exc) from exc
    return _control_response(
        batch_id=result.batch_id,
        status_value=result.batch_status,
        affected_count=result.affected_count,
    )


@router.post(
    "/{batch_id}:cancel",
    response_model=BatchControlResponse,
    summary="Cancel unstarted report batch work",
    description=(
        "Cancels batch items that have not created report jobs. Use this endpoint to stop "
        "remaining batch work while preserving already-created report jobs for audit and "
        "downstream lifecycle reconciliation."
    ),
    openapi_extra={
        "responses": {
            "200": {"content": {"application/json": {"example": BATCH_CONTROL_RESPONSE_EXAMPLE}}}
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_batch_not_found",
            description="Returned when the requested report batch does not exist.",
        )
    },
)
async def cancel_report_batch(
    batch_id: Annotated[str, Path(description="Opaque durable report batch identifier.")],
    ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    _caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchControlResponse:
    try:
        result = ledger.cancel_batch(batch_id=batch_id)
    except ValueError as exc:
        raise _not_found_error(exc) from exc
    return _control_response(
        batch_id=result.batch_id,
        status_value=result.batch_status,
        affected_count=result.affected_count,
    )


@router.post(
    "/{batch_id}:retry-failed",
    response_model=BatchControlResponse,
    summary="Retry eligible failed report batch items",
    description=(
        "Resets only retryable failed batch items whose retry window is open and whose attempt "
        "count remains below policy. Items with already-created report jobs are not requeued."
    ),
    openapi_extra={
        "responses": {
            "200": {"content": {"application/json": {"example": BATCH_CONTROL_RESPONSE_EXAMPLE}}}
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_batch_not_found",
            description="Returned when the requested report batch does not exist.",
        )
    },
)
async def retry_failed_report_batch_items(
    batch_id: Annotated[str, Path(description="Opaque durable report batch identifier.")],
    ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    _caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchControlResponse:
    try:
        result = ledger.retry_failed_items(batch_id=batch_id)
    except ValueError as exc:
        raise _not_found_error(exc) from exc
    return _control_response(
        batch_id=result.batch_id,
        status_value=result.batch_status,
        affected_count=result.affected_count,
    )


@router.post(
    "/{batch_id}:recover-expired-leases",
    response_model=BatchRecoveryResponse,
    summary="Recover expired report batch item leases",
    description=(
        "Moves expired unjobbed item leases to recovery-pending posture so they can be safely "
        "redispatched. The operation is idempotent and does not duplicate existing report jobs."
    ),
    openapi_extra={
        "responses": {
            "200": {"content": {"application/json": {"example": BATCH_RECOVERY_RESPONSE_EXAMPLE}}}
        }
    },
    responses={
        **_error_response(
            404,
            example_key="report_batch_not_found",
            description="Returned when the requested report batch does not exist.",
        )
    },
)
async def recover_expired_report_batch_leases(
    batch_id: Annotated[str, Path(description="Opaque durable report batch identifier.")],
    ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    _caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchRecoveryResponse:
    try:
        result = ledger.recover_expired_leases(batch_id=batch_id)
        batch = ledger.get_batch(batch_id)
    except ValueError as exc:
        raise _not_found_error(exc) from exc
    return BatchRecoveryResponse(
        batch_id=result.batch_id,
        status=batch.status,
        recovered_count=result.recovered_count,
        recovery_pending_item_ids=result.recovery_pending_item_ids,
        status_url=_status_url(result.batch_id),
    )
