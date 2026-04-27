from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram

from app.report_batch_orchestrator.models import BatchPressureSnapshot

METRIC_OPERATION_LABEL = "operation"
METRIC_STATUS_LABEL = "status"
METRIC_FAILURE_CATEGORY_LABEL = "failure_category"
METRIC_ITEM_STATE_LABEL = "item_state"
METRIC_SCHEDULER_OUTCOME_LABEL = "outcome"
METRIC_PRESSURE_STATE_LABEL = "pressure_state"

REPORTING_METRIC_LABELS = frozenset(
    {
        METRIC_OPERATION_LABEL,
        METRIC_STATUS_LABEL,
        METRIC_FAILURE_CATEGORY_LABEL,
        METRIC_ITEM_STATE_LABEL,
        METRIC_SCHEDULER_OUTCOME_LABEL,
        METRIC_PRESSURE_STATE_LABEL,
    }
)
FORBIDDEN_METRIC_LABELS = frozenset(
    {
        "account_id",
        "archive_document_id",
        "batch_id",
        "batch_item_id",
        "booking_center_code",
        "bucket",
        "client_id",
        "client_name",
        "correlation_id",
        "document_id",
        "idempotency_key",
        "portfolio_id",
        "portfolio_name",
        "raw_upstream_payload",
        "render_job_id",
        "report_job_id",
        "request_id",
        "snapshot_id",
        "storage_key",
        "tenant_id",
        "trace_id",
    }
)

IMPLEMENTED_REPORTING_OPERATIONS = frozenset(
    {
        "archive_handoff",
        "batch_scheduler_pass",
        "batch_worker_run",
        "report_job_submission",
        "render_handoff",
        "snapshot_capture",
    }
)
RESERVED_REPORTING_OPERATIONS = frozenset(
    {
        "regenerate_command",
        "replay_command",
        "rerender_command",
        "stuck_state_scan",
    }
)
REPORTING_OPERATION_STATUSES = frozenset(
    {
        "accepted",
        "archived",
        "cancelled",
        "completed",
        "completed_with_warnings",
        "data_ready",
        "failed",
        "materialized",
        "skipped",
        "succeeded",
    }
)


@dataclass(frozen=True)
class ReportingMetricContract:
    name: str
    metric_type: str
    labels: tuple[str, ...]
    implemented: bool
    description: str


REPORTING_METRIC_CONTRACTS: tuple[ReportingMetricContract, ...] = (
    ReportingMetricContract(
        name="lotus_report_operations_total",
        metric_type="counter",
        labels=(METRIC_OPERATION_LABEL, METRIC_STATUS_LABEL, METRIC_FAILURE_CATEGORY_LABEL),
        implemented=True,
        description=(
            "Counts supported report, snapshot, render, archive, batch worker, and scheduler "
            "operations by bounded operation, lifecycle status, and failure category."
        ),
    ),
    ReportingMetricContract(
        name="lotus_report_operation_duration_seconds",
        metric_type="histogram",
        labels=(METRIC_OPERATION_LABEL, METRIC_STATUS_LABEL, METRIC_FAILURE_CATEGORY_LABEL),
        implemented=True,
        description=(
            "Measures duration for supported report operations using bounded labels only."
        ),
    ),
    ReportingMetricContract(
        name="lotus_report_batch_runtime_last_items",
        metric_type="gauge",
        labels=(METRIC_ITEM_STATE_LABEL,),
        implemented=True,
        description=(
            "Stores item counts from the latest bounded batch-worker pass without item, batch, "
            "portfolio, tenant, or trace identifiers."
        ),
    ),
    ReportingMetricContract(
        name="lotus_report_batch_scheduler_last_schedules",
        metric_type="gauge",
        labels=(METRIC_SCHEDULER_OUTCOME_LABEL,),
        implemented=True,
        description=(
            "Stores schedule counts from the latest bounded scheduler pass without schedule, "
            "batch, portfolio, tenant, or trace identifiers."
        ),
    ),
    ReportingMetricContract(
        name="lotus_report_batch_pressure_last_counts",
        metric_type="gauge",
        labels=(METRIC_PRESSURE_STATE_LABEL,),
        implemented=True,
        description=(
            "Stores bounded durable batch pressure counts from the latest worker or operator pass "
            "without batch, portfolio, tenant, retry token, or trace identifiers."
        ),
    ),
    ReportingMetricContract(
        name="lotus_report_replay_operations_total",
        metric_type="counter",
        labels=(METRIC_OPERATION_LABEL, METRIC_STATUS_LABEL, METRIC_FAILURE_CATEGORY_LABEL),
        implemented=False,
        description=(
            "Reserved for future replay/rerender/regenerate commands. It must not be emitted "
            "until those command paths are implementation-backed."
        ),
    ),
)

_REPORT_OPERATIONS_TOTAL = Counter(
    "lotus_report_operations_total",
    REPORTING_METRIC_CONTRACTS[0].description,
    [METRIC_OPERATION_LABEL, METRIC_STATUS_LABEL, METRIC_FAILURE_CATEGORY_LABEL],
)
_REPORT_OPERATION_DURATION_SECONDS = Histogram(
    "lotus_report_operation_duration_seconds",
    REPORTING_METRIC_CONTRACTS[1].description,
    [METRIC_OPERATION_LABEL, METRIC_STATUS_LABEL, METRIC_FAILURE_CATEGORY_LABEL],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)
_BATCH_RUNTIME_LAST_ITEMS = Gauge(
    "lotus_report_batch_runtime_last_items",
    REPORTING_METRIC_CONTRACTS[2].description,
    [METRIC_ITEM_STATE_LABEL],
)
_BATCH_SCHEDULER_LAST_SCHEDULES = Gauge(
    "lotus_report_batch_scheduler_last_schedules",
    REPORTING_METRIC_CONTRACTS[3].description,
    [METRIC_SCHEDULER_OUTCOME_LABEL],
)
_BATCH_PRESSURE_LAST_COUNTS = Gauge(
    "lotus_report_batch_pressure_last_counts",
    REPORTING_METRIC_CONTRACTS[4].description,
    [METRIC_PRESSURE_STATE_LABEL],
)


def validate_reporting_metric_contracts() -> None:
    names = [contract.name for contract in REPORTING_METRIC_CONTRACTS]
    if len(names) != len(set(names)):
        raise ValueError("duplicate_reporting_metric_name")
    for contract in REPORTING_METRIC_CONTRACTS:
        _validate_labels(contract.labels)
        if contract.implemented and contract.name == "lotus_report_replay_operations_total":
            raise ValueError("reserved_replay_metric_marked_implemented")


def record_report_operation(
    *,
    operation: str,
    status: str,
    failure_category: str | None = None,
    duration_seconds: float | None = None,
) -> None:
    operation_label = _implemented_operation(operation)
    status_label = _bounded_status(status)
    failure_label = _bounded_failure_category(failure_category)
    _REPORT_OPERATIONS_TOTAL.labels(
        operation=operation_label,
        status=status_label,
        failure_category=failure_label,
    ).inc()
    if duration_seconds is not None:
        _REPORT_OPERATION_DURATION_SECONDS.labels(
            operation=operation_label,
            status=status_label,
            failure_category=failure_label,
        ).observe(max(0.0, duration_seconds))


def record_batch_worker_metrics(
    *,
    recovered_count: int,
    leased_count: int,
    dispatched_count: int,
    executed_count: int,
    skipped_reason: str | None = None,
    status: str | None = None,
    failure_category: str | None = None,
    duration_seconds: float | None = None,
) -> None:
    if status is None:
        status = "skipped" if skipped_reason else "completed"
        failure_category = _failure_category_from_skip(skipped_reason)

    record_report_operation(
        operation="batch_worker_run",
        status=status,
        failure_category=failure_category,
        duration_seconds=duration_seconds,
    )
    _BATCH_RUNTIME_LAST_ITEMS.labels(item_state="recovered").set(max(0, recovered_count))
    _BATCH_RUNTIME_LAST_ITEMS.labels(item_state="leased").set(max(0, leased_count))
    _BATCH_RUNTIME_LAST_ITEMS.labels(item_state="dispatched").set(max(0, dispatched_count))
    _BATCH_RUNTIME_LAST_ITEMS.labels(item_state="executed").set(max(0, executed_count))


def record_batch_scheduler_metrics(
    *,
    attempted_count: int,
    materialized_count: int,
    skipped_count: int,
    duration_seconds: float | None = None,
) -> None:
    record_report_operation(
        operation="batch_scheduler_pass",
        status="completed",
        duration_seconds=duration_seconds,
    )
    _BATCH_SCHEDULER_LAST_SCHEDULES.labels(outcome="attempted").set(max(0, attempted_count))
    _BATCH_SCHEDULER_LAST_SCHEDULES.labels(outcome="materialized").set(max(0, materialized_count))
    _BATCH_SCHEDULER_LAST_SCHEDULES.labels(outcome="skipped").set(max(0, skipped_count))


def record_batch_pressure_metrics(snapshot: BatchPressureSnapshot) -> None:
    _BATCH_PRESSURE_LAST_COUNTS.labels(pressure_state="runnable_batches").set(
        max(0, snapshot.runnable_batches)
    )
    _BATCH_PRESSURE_LAST_COUNTS.labels(pressure_state="active_batches").set(
        max(0, snapshot.active_batches)
    )
    _BATCH_PRESSURE_LAST_COUNTS.labels(pressure_state="active_items").set(
        max(0, snapshot.active_items)
    )
    _BATCH_PRESSURE_LAST_COUNTS.labels(pressure_state="dispatch_ready_items").set(
        max(0, snapshot.dispatch_ready_items)
    )
    _BATCH_PRESSURE_LAST_COUNTS.labels(pressure_state="retry_ready_items").set(
        max(0, snapshot.retry_ready_items)
    )
    _BATCH_PRESSURE_LAST_COUNTS.labels(pressure_state="recovery_pending_items").set(
        max(0, snapshot.recovery_pending_items)
    )


def _validate_labels(labels: Iterable[str]) -> None:
    label_set = set(labels)
    forbidden = label_set & FORBIDDEN_METRIC_LABELS
    if forbidden:
        raise ValueError(f"forbidden_metric_label:{sorted(forbidden)[0]}")
    unsupported = label_set - REPORTING_METRIC_LABELS
    if unsupported:
        raise ValueError(f"unsupported_metric_label:{sorted(unsupported)[0]}")


def _implemented_operation(operation: str) -> str:
    if operation not in IMPLEMENTED_REPORTING_OPERATIONS:
        raise ValueError(f"unsupported_reporting_metric_operation:{operation}")
    return operation


def _bounded_status(status: str) -> str:
    if status in REPORTING_OPERATION_STATUSES:
        return status
    return "failed"


def _bounded_failure_category(failure_category: str | None) -> str:
    if not failure_category:
        return "none"
    normalized = failure_category.strip().lower().replace("-", "_")
    if not normalized:
        return "none"
    if len(normalized) > 80:
        return "other"
    if not all(character.isalnum() or character == "_" for character in normalized):
        return "other"
    return normalized


def _failure_category_from_skip(skipped_reason: str | None) -> str:
    if not skipped_reason:
        return "none"
    if skipped_reason.startswith("batch_not_runnable:"):
        return "batch_not_runnable"
    return "skipped"
