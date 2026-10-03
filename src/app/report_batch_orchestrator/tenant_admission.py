"""Single owner of report-batch tenant admission.

Every externally invocable batch mutation, control, replay, or execution path admits
the caller through this module before touching durable state. Admission is fail-closed
and deliberately indistinguishable from absence: an identifier owned by another tenant
raises the same ``report_batch_not_found`` signal as an identifier that does not exist,
so cross-tenant existence is never disclosed through the error contract.

The rule lives here rather than in the HTTP layer because the batch worker and the
replay service are invoked by background processes that never pass through a router.
"""

from __future__ import annotations

from typing import Protocol

from app.report_batch_orchestrator.models import ReportBatchRecord
from app.reporting_jobs.models import ReportCallerContext

BATCH_NOT_FOUND = "report_batch_not_found"
SCHEDULER_NOT_FOUND = "batch_scheduler_not_found"


class SchedulerTenantAdmissionError(ValueError):
    """Product-safe refusal with no configured scheduler identity disclosure."""

    def __init__(self) -> None:
        super().__init__(SCHEDULER_NOT_FOUND)


class SchedulerScope(Protocol):
    @property
    def tenant_id(self) -> str: ...

    @property
    def region(self) -> str: ...

    @property
    def booking_center_code(self) -> str | None: ...


def admit_scheduler(
    scope: SchedulerScope,
    *,
    caller_context: ReportCallerContext,
) -> None:
    """Admit the configured scope before disclosure or execution identity replacement.

    Ordinary reporting capability grants no cross-scope operator authority. Exact
    scope matches the stored-definition policy, including an absent booking center.
    The daemon's configured context passes the same guard without a bypass.
    """
    if (
        not scope.tenant_id.strip()
        or not scope.region.strip()
        or scope.tenant_id != caller_context.tenant_id
        or scope.region != caller_context.region
        or scope.booking_center_code != caller_context.booking_center_code
    ):
        raise SchedulerTenantAdmissionError()


class BatchTenantAdmissionLedger(Protocol):
    """Minimum durable surface tenant admission needs."""

    def get_batch(self, batch_id: str) -> ReportBatchRecord: ...


def admit_batch(
    record: ReportBatchRecord,
    *,
    caller_context: ReportCallerContext,
) -> ReportBatchRecord:
    """Return the batch when the caller owns it, otherwise fail closed as not found.

    Raises:
        ValueError: ``report_batch_not_found`` when the persisted batch tenant differs
            from the caller tenant. The message is identical to the unknown-identifier
            signal so callers cannot distinguish the two cases.
    """

    if record.tenant_id != caller_context.tenant_id:
        raise ValueError(BATCH_NOT_FOUND)
    return record


def load_admitted_batch(
    *,
    ledger: BatchTenantAdmissionLedger,
    batch_id: str,
    caller_context: ReportCallerContext,
) -> ReportBatchRecord:
    """Load a batch and admit the caller in one step.

    Raises:
        ValueError: ``report_batch_not_found`` for both unknown and cross-tenant
            identifiers. Ledger lookups raise the same signal for unknown identifiers,
            so the two paths are already indistinguishable to the caller.
    """

    return admit_batch(ledger.get_batch(batch_id), caller_context=caller_context)
