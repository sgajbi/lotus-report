from __future__ import annotations

from collections.abc import Iterable

from app.report_batch_orchestrator.models import BatchItemStatus, BatchStatus

BATCH_ACTIVE_STATUSES: frozenset[BatchStatus] = frozenset({"materialized", "running"})
BATCH_COMPLETED_STATUSES: frozenset[BatchStatus] = frozenset(
    {"completed", "completed_with_failures", "failed"}
)
BATCH_STATUS_REFRESH_BLOCKED_STATUSES: frozenset[BatchStatus] = frozenset({"paused", "cancelled"})
BATCH_ITEM_CANCEL_ELIGIBLE_STATUSES: frozenset[BatchItemStatus] = frozenset(
    {"materialized", "recovery_pending", "failed_retryable", "leased"}
)
BATCH_ITEM_ACTIVE_STATUSES: frozenset[BatchItemStatus] = frozenset(
    {"leased", "waiting_on_report_job"}
)


def batch_item_failure_outcome(
    *,
    retryable: bool,
    attempt_count: int,
    max_attempts: int,
) -> tuple[BatchItemStatus, bool]:
    status: BatchItemStatus = (
        "failed_retryable" if retryable and attempt_count < max_attempts else "failed_terminal"
    )
    return status, status == "failed_retryable"


def reconciled_batch_status(item_statuses: Iterable[str]) -> BatchStatus | None:
    statuses = set(item_statuses)
    if not statuses:
        return None
    if statuses <= {"succeeded", "cancelled"}:
        return "completed" if "cancelled" not in statuses else "cancelled"
    if statuses <= {"succeeded", "failed_terminal", "cancelled"}:
        return "completed_with_failures"
    if "failed_retryable" in statuses and statuses <= {"succeeded", "failed_retryable"}:
        return "failed"
    return None
