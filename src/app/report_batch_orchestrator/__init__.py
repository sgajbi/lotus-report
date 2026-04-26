"""Batch reporting orchestration boundary.

RFC-0104 currently provides durable batch materialization, deterministic cycle
identity, and internal dispatch primitives. Operator-facing APIs, retry, and
recovery remain future slices.
"""

from app.report_batch_orchestrator.contracts import (
    BATCH_CAPABILITY_KEY,
    BATCH_FREQUENCIES,
    BATCH_RUNTIME_SUPPORTED,
    BATCH_SELECTOR_MODES,
)
from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher, evaluate_back_pressure
from app.report_batch_orchestrator.ledger import (
    BatchIdempotencyConflictError,
    MissingBatchIdempotencyKeyError,
    ReportBatchLedger,
    compute_batch_request_hash,
)
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    BatchCycle,
    BatchCycleRequest,
    BatchDispatchPolicy,
    BatchDispatchResult,
    BatchRuntimeLoad,
    PortfolioBatchCandidate,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.schedule import (
    BatchScheduleValidationError,
    materialize_cycle,
    scheduled_batch_idempotency_key,
)
from app.report_batch_orchestrator.selector import (
    BatchSelectorValidationError,
    materialize_portfolios,
)

__all__ = [
    "BATCH_CAPABILITY_KEY",
    "BATCH_FREQUENCIES",
    "BATCH_RUNTIME_SUPPORTED",
    "BATCH_SELECTOR_MODES",
    "BatchCreateRequest",
    "BatchCycle",
    "BatchCycleRequest",
    "BatchDispatchPolicy",
    "BatchDispatchResult",
    "BatchIdempotencyConflictError",
    "BatchRuntimeLoad",
    "BatchScheduleValidationError",
    "BatchSelectorValidationError",
    "MissingBatchIdempotencyKeyError",
    "PortfolioBatchCandidate",
    "ReportBatchDispatcher",
    "ReportBatchItemRecord",
    "ReportBatchLedger",
    "ReportBatchRecord",
    "compute_batch_request_hash",
    "evaluate_back_pressure",
    "materialize_cycle",
    "materialize_portfolios",
    "scheduled_batch_idempotency_key",
]
