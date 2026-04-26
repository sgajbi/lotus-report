"""Batch reporting orchestration boundary.

RFC-0104 currently provides durable batch materialization, deterministic cycle
identity, internal dispatch/control primitives, internal bounded worker
execution, a bounded runtime-pass primitive, daemonized worker process
entrypoint, daemonized scheduler process entrypoint, and certified
materialization, status, and control APIs. Public runtime surfaces remain
future slices.
"""

from app.report_batch_orchestrator.contracts import (
    BATCH_CAPABILITY_KEY,
    BATCH_CONTROL_API_CAPABILITY_KEY,
    BATCH_FREQUENCIES,
    BATCH_MATERIALIZATION_API_CAPABILITY_KEY,
    BATCH_RUNTIME_SUPPORTED,
    BATCH_SELECTOR_MODES,
)
from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher, evaluate_back_pressure
from app.report_batch_orchestrator.execution import (
    BatchItemExecutionResult,
    ReportBatchExecutionService,
)
from app.report_batch_orchestrator.ledger import (
    BatchIdempotencyConflictError,
    MissingBatchIdempotencyKeyError,
    ReportBatchLedger,
    compute_batch_request_hash,
)
from app.report_batch_orchestrator.models import (
    BatchControlResponse,
    BatchCreateRequest,
    BatchCycle,
    BatchCycleRequest,
    BatchDispatchPolicy,
    BatchDispatchResult,
    BatchHandleResponse,
    BatchItemStatusResponse,
    BatchRecoveryResponse,
    BatchRuntimeLoad,
    BatchStatusResponse,
    BatchWorkerItemExecutionResponse,
    BatchWorkerRunRequest,
    BatchWorkerRunResponse,
    PortfolioBatchCandidate,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.runtime import BatchRuntimePassResult, ReportBatchRuntime
from app.report_batch_orchestrator.schedule import (
    BatchScheduleValidationError,
    materialize_cycle,
    scheduled_batch_idempotency_key,
)
from app.report_batch_orchestrator.scheduler import (
    BatchScheduleDefinition,
    BatchSchedulerConfig,
    BatchSchedulerRunResult,
    ReportBatchScheduler,
)
from app.report_batch_orchestrator.selector import (
    BatchSelectorValidationError,
    materialize_portfolios,
)
from app.report_batch_orchestrator.worker import BatchWorkerRunResult, ReportBatchWorker

__all__ = [
    "BATCH_CAPABILITY_KEY",
    "BATCH_CONTROL_API_CAPABILITY_KEY",
    "BATCH_FREQUENCIES",
    "BATCH_MATERIALIZATION_API_CAPABILITY_KEY",
    "BATCH_RUNTIME_SUPPORTED",
    "BATCH_SELECTOR_MODES",
    "BatchCreateRequest",
    "BatchControlResponse",
    "BatchCycle",
    "BatchCycleRequest",
    "BatchDispatchPolicy",
    "BatchDispatchResult",
    "BatchIdempotencyConflictError",
    "BatchItemExecutionResult",
    "BatchHandleResponse",
    "BatchItemStatusResponse",
    "BatchRecoveryResponse",
    "BatchRuntimeLoad",
    "BatchRuntimePassResult",
    "BatchScheduleDefinition",
    "BatchScheduleValidationError",
    "BatchSelectorValidationError",
    "BatchSchedulerConfig",
    "BatchSchedulerRunResult",
    "BatchStatusResponse",
    "BatchWorkerRunResult",
    "BatchWorkerRunRequest",
    "BatchWorkerRunResponse",
    "BatchWorkerItemExecutionResponse",
    "MissingBatchIdempotencyKeyError",
    "PortfolioBatchCandidate",
    "ReportBatchDispatcher",
    "ReportBatchExecutionService",
    "ReportBatchWorker",
    "ReportBatchRuntime",
    "ReportBatchScheduler",
    "ReportBatchItemRecord",
    "ReportBatchLedger",
    "ReportBatchRecord",
    "compute_batch_request_hash",
    "evaluate_back_pressure",
    "materialize_cycle",
    "materialize_portfolios",
    "scheduled_batch_idempotency_key",
]
