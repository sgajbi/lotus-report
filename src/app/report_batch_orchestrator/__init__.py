"""Batch reporting orchestration boundary.

RFC-0104 Slice 2 provides durable batch and item materialization primitives.
Operator-facing APIs, scheduling, dispatch, retry, and recovery remain future
slices.
"""

from app.report_batch_orchestrator.contracts import (
    BATCH_CAPABILITY_KEY,
    BATCH_FREQUENCIES,
    BATCH_RUNTIME_SUPPORTED,
    BATCH_SELECTOR_MODES,
)
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
    "BatchIdempotencyConflictError",
    "BatchScheduleValidationError",
    "BatchSelectorValidationError",
    "MissingBatchIdempotencyKeyError",
    "PortfolioBatchCandidate",
    "ReportBatchItemRecord",
    "ReportBatchLedger",
    "ReportBatchRecord",
    "compute_batch_request_hash",
    "materialize_cycle",
    "materialize_portfolios",
    "scheduled_batch_idempotency_key",
]
