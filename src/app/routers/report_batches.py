from time import perf_counter
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
    BATCH_WORKER_RUN_REQUEST_EXAMPLE,
    BATCH_WORKER_RUN_RESPONSE_EXAMPLE,
    BatchControlResponse,
    BatchCreateRequest,
    BatchHandleResponse,
    BatchItemStatusResponse,
    BatchPressureSnapshot,
    BatchRecoveryResponse,
    BatchRetryPolicy,
    BatchStatus,
    BatchStatusResponse,
    BatchWorkerItemExecutionResponse,
    BatchWorkerRunRequest,
    BatchWorkerRunResponse,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.scheduler import (
    BATCH_SCHEDULE_LIST_RESPONSE_EXAMPLE,
    BATCH_SCHEDULER_RUN_REQUEST_EXAMPLE,
    BATCH_SCHEDULER_RUN_RESPONSE_EXAMPLE,
    BatchScheduleConfigError,
    BatchScheduleListResponse,
    BatchSchedulerConfig,
    BatchSchedulerRunRequest,
    BatchSchedulerRunResponse,
    batch_schedule_list_response,
    batch_scheduler_caller_context,
    batch_scheduler_config_from_settings,
    batch_scheduler_run_response,
)
from app.report_batch_orchestrator.selector import BatchSelectorValidationError
from app.report_batch_orchestrator.service import (
    get_report_batch_ledger,
    get_report_batch_scheduler,
    get_report_batch_worker,
)
from app.report_batch_orchestrator.worker import BatchWorkerRunResult
from app.reporting_jobs.models import ApiErrorResponse, ReportCallerContext
from app.reporting_metrics import (
    record_batch_pressure_metrics,
    record_batch_scheduler_metrics,
    record_batch_worker_metrics,
)
from app.routers.caller_context import caller_context_dependency

router = APIRouter(prefix="/reports/batches", tags=["Report Batches"])
schedules_router = APIRouter(prefix="/reports/batch-schedules", tags=["Report Batch Schedules"])


class ReportBatchLedgerPort(Protocol):
    def create_batch(
        self,
        *,
        request: BatchCreateRequest,
        caller_context: Any,
        idempotency_key: str | None,
    ) -> ReportBatchRecord: ...

    def get_batch(self, batch_id: str) -> ReportBatchRecord: ...

    def get_batch_item(
        self,
        batch_id: str,
        batch_item_id: str,
    ) -> ReportBatchItemRecord: ...

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

    def batch_pressure_snapshot(self) -> BatchPressureSnapshot: ...


class ReportBatchWorkerPort(Protocol):
    async def run_once(
        self,
        *,
        batch_id: str,
        caller_context: ReportCallerContext,
        worker_id: str,
        runtime_load: Any | None = None,
        dispatch_policy: Any | None = None,
        recover_expired_leases: bool = True,
    ) -> BatchWorkerRunResult: ...


class ReportBatchSchedulerPort(Protocol):
    async def run_due_schedules(
        self,
        *,
        config: BatchSchedulerConfig,
        caller_context: ReportCallerContext,
    ) -> Any: ...


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
    "report_batch_item_not_found": {
        "detail": {
            "code": "report_batch_item_not_found",
            "message": "Report batch item was not found.",
        }
    },
    "batch_worker_run_failed": {
        "detail": {
            "code": "batch_worker_run_failed",
            "message": "Report batch run could not be completed.",
        }
    },
    "invalid_batch_scheduler_config": {
        "detail": {
            "code": "invalid_batch_scheduler_config",
            "message": "Configured report batch schedules could not be loaded.",
        }
    },
    "batch_scheduler_run_failed": {
        "detail": {
            "code": "batch_scheduler_run_failed",
            "message": "Report batch scheduler pass could not be completed.",
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


def get_report_batch_scheduler_config() -> BatchSchedulerConfig:
    try:
        return batch_scheduler_config_from_settings()
    except BatchScheduleConfigError as exc:
        raise _scheduler_config_error(exc) from exc


def _record_to_handle(record: ReportBatchRecord) -> BatchHandleResponse:
    return BatchHandleResponse(
        batch_id=record.batch_id,
        status=record.status,
        status_url=_status_url(record.batch_id),
        idempotency_key=record.idempotency_key,
        item_count=record.item_count,
    )


def _record_item_to_status(item: Any) -> BatchItemStatusResponse:
    return BatchItemStatusResponse(
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
        items=[_record_item_to_status(item) for item in record.items],
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


def _worker_run_response(result: BatchWorkerRunResult) -> BatchWorkerRunResponse:
    return BatchWorkerRunResponse(
        batch_id=result.batch_id,
        status=result.batch_status_after,
        batch_status_before=result.batch_status_before,
        batch_status_after=result.batch_status_after,
        recovered_count=result.recovered_count,
        leased_count=result.leased_count,
        dispatched_count=result.dispatched_count,
        executed_count=result.executed_count,
        report_job_ids=result.report_job_ids,
        back_pressure_reasons=result.back_pressure_reasons,
        skipped_reason=result.skipped_reason,
        execution_results=[
            BatchWorkerItemExecutionResponse(
                batch_item_id=item.batch_item_id,
                report_job_id=item.report_job_id,
                item_status=item.item_status,
                report_job_status=item.report_job_status,
                failure_category=item.failure_category,
                retry_eligible=item.retry_eligible,
            )
            for item in result.execution_results
        ],
        status_url=_status_url(result.batch_id),
    )


def _scheduler_config_error(exc: BatchScheduleConfigError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": exc.code,
            "message": exc.message,
        },
    )


@schedules_router.get(
    "",
    response_model=BatchScheduleListResponse,
    summary="List governed report batch schedules",
    description=(
        "Returns the currently configured report batch schedules from the governed scheduler "
        "configuration source. This endpoint is read-only: schedules remain config-backed and "
        "are not created, edited, or deleted through the API."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": BATCH_SCHEDULE_LIST_RESPONSE_EXAMPLE,
                        "examples": {
                            "configured_schedules": {
                                "summary": "Configured schedules",
                                "value": BATCH_SCHEDULE_LIST_RESPONSE_EXAMPLE,
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
            example_key="invalid_batch_scheduler_config",
            description="Returned when the configured scheduler JSON cannot be loaded.",
        )
    },
)
async def list_report_batch_schedules(
    config: BatchSchedulerConfig = Depends(get_report_batch_scheduler_config),
    _caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchScheduleListResponse:
    try:
        return batch_schedule_list_response(config)
    except BatchScheduleConfigError as exc:
        raise _scheduler_config_error(exc) from exc


@schedules_router.post(
    ":run-due",
    response_model=BatchSchedulerRunResponse,
    summary="Run one bounded report batch scheduler pass",
    description=(
        "Runs one bounded operator-triggered scheduler pass over enabled configured schedules. "
        "The pass resolves configured schedule selectors, materializes durable idempotent batches, "
        "and returns product-safe materialization results. It does not execute batch items; the "
        "batch worker remains responsible for dispatch, render, archive, and reconciliation."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": BATCH_SCHEDULER_RUN_REQUEST_EXAMPLE,
                    "examples": {
                        "run_due": {
                            "summary": "Run due configured schedules once",
                            "value": BATCH_SCHEDULER_RUN_REQUEST_EXAMPLE,
                        }
                    },
                }
            }
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": BATCH_SCHEDULER_RUN_RESPONSE_EXAMPLE,
                        "examples": {
                            "materialized": {
                                "summary": "Materialized scheduled batch",
                                "value": BATCH_SCHEDULER_RUN_RESPONSE_EXAMPLE,
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
            example_key="invalid_batch_scheduler_config",
            description="Returned when the configured scheduler JSON cannot be loaded.",
        ),
        **_error_response(
            409,
            example_key="batch_scheduler_run_failed",
            description="Returned when the scheduler pass cannot safely materialize schedules.",
        ),
    },
)
async def run_due_report_batch_schedules(
    request: BatchSchedulerRunRequest,
    scheduler: ReportBatchSchedulerPort = Depends(get_report_batch_scheduler),
    config: BatchSchedulerConfig = Depends(get_report_batch_scheduler_config),
    _operator_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchSchedulerRunResponse:
    started_at = perf_counter()
    scheduler_context = batch_scheduler_caller_context(
        config,
        pass_sequence=request.pass_sequence,
    )
    try:
        result = await scheduler.run_due_schedules(
            config=config,
            caller_context=scheduler_context,
        )
    except BatchScheduleConfigError as exc:
        raise _scheduler_config_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "batch_scheduler_run_failed",
                "message": "Report batch scheduler pass could not be completed.",
            },
        ) from exc
    record_batch_scheduler_metrics(
        attempted_count=result.attempted_count,
        materialized_count=len(result.materialized),
        skipped_count=len(result.skipped_schedule_ids),
        duration_seconds=perf_counter() - started_at,
    )
    return batch_scheduler_run_response(result=result, caller_context=scheduler_context)


def _not_found_error(exc: ValueError) -> HTTPException:
    if str(exc) == "report_batch_not_found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=BATCH_API_ERROR_RESPONSE_EXAMPLES["report_batch_not_found"]["detail"],
        )
    if str(exc) == "report_batch_item_not_found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=BATCH_API_ERROR_RESPONSE_EXAMPLES["report_batch_item_not_found"]["detail"],
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


@router.get(
    "/{batch_id}/items/{batch_item_id}",
    response_model=BatchItemStatusResponse,
    summary="Get report batch item status",
    description=(
        "Returns product-safe status for a specific item in a durable report batch, including "
        "execution posture and retry metadata."
    ),
    responses={
        404: {
            "model": ApiErrorResponse,
            "description": (
                "Returned when the requested report batch or report batch item does not exist."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "report_batch_not_found": {
                            "summary": "Unknown batch",
                            "value": BATCH_API_ERROR_RESPONSE_EXAMPLES["report_batch_not_found"],
                        },
                        "report_batch_item_not_found": {
                            "summary": "Unknown batch item",
                            "value": BATCH_API_ERROR_RESPONSE_EXAMPLES[
                                "report_batch_item_not_found"
                            ],
                        },
                    }
                }
            },
        }
    },
)
async def get_report_batch_item_status(
    batch_id: Annotated[
        str,
        Path(description="Opaque durable report batch identifier.", examples=["rbch_example"]),
    ],
    batch_item_id: Annotated[
        str,
        Path(description="Opaque durable report batch item identifier.", examples=["rbci_example"]),
    ],
    ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    _caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchItemStatusResponse:
    try:
        return _record_item_to_status(
            ledger.get_batch_item(batch_id=batch_id, batch_item_id=batch_item_id)
        )
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


@router.post(
    "/{batch_id}:run-once",
    response_model=BatchWorkerRunResponse,
    summary="Run one bounded report batch worker pass",
    description=(
        "Runs one bounded operator-controlled pass for a durable report batch. The pass can "
        "recover expired unjobbed leases, dispatch eligible items under back-pressure policy, "
        "and advance waiting report jobs through snapshot, render, archive, and batch-item "
        "reconciliation. This is a single-batch operator action, not a scheduler loop, gateway "
        "surface, or Workbench feature."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": BATCH_WORKER_RUN_REQUEST_EXAMPLE,
                    "examples": {
                        "bounded_worker_run": {
                            "summary": "Bounded worker run",
                            "value": BATCH_WORKER_RUN_REQUEST_EXAMPLE,
                        }
                    },
                }
            }
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": BATCH_WORKER_RUN_RESPONSE_EXAMPLE,
                        "examples": {
                            "completed_run": {
                                "summary": "Completed bounded worker run",
                                "value": BATCH_WORKER_RUN_RESPONSE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        },
    },
    responses={
        **_error_response(
            404,
            example_key="report_batch_not_found",
            description="Returned when the requested report batch does not exist.",
        ),
        **_error_response(
            409,
            example_key="batch_worker_run_failed",
            description=(
                "Returned when the bounded worker run cannot complete because durable batch "
                "state or linked report-job state is inconsistent."
            ),
        ),
    },
)
async def run_report_batch_once(
    request: BatchWorkerRunRequest,
    batch_id: Annotated[str, Path(description="Opaque durable report batch identifier.")],
    worker: ReportBatchWorkerPort = Depends(get_report_batch_worker),
    batch_ledger: ReportBatchLedgerPort = Depends(get_report_batch_ledger),
    caller_context: ReportCallerContext = Depends(caller_context_dependency),
) -> BatchWorkerRunResponse:
    started_at = perf_counter()
    try:
        result = await worker.run_once(
            batch_id=batch_id,
            caller_context=caller_context,
            worker_id=request.worker_id,
            runtime_load=request.runtime_load,
            dispatch_policy=request.dispatch_policy,
            recover_expired_leases=request.recover_expired_leases,
        )
    except ValueError as exc:
        raise _not_found_error(exc) from exc
    except RuntimeError as exc:
        record_batch_worker_metrics(
            recovered_count=0,
            leased_count=0,
            dispatched_count=0,
            executed_count=0,
            status="failed",
            failure_category="batch_worker_runtime_error",
            duration_seconds=perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "batch_worker_run_failed",
                "message": "Report batch run could not be completed.",
            },
        ) from exc
    record_batch_worker_metrics(
        recovered_count=result.recovered_count,
        leased_count=result.leased_count,
        dispatched_count=result.dispatched_count,
        executed_count=result.executed_count,
        skipped_reason=result.skipped_reason,
        duration_seconds=perf_counter() - started_at,
    )
    record_batch_pressure_metrics(batch_ledger.batch_pressure_snapshot())
    return _worker_run_response(result)
