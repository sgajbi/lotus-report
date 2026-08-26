from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.report_batch_orchestrator.models import (
    BatchDispatchPolicy,
    BatchPressureSnapshot,
    BatchRuntimeLoad,
)
from app.report_batch_orchestrator.worker import BatchWorkerRunResult, ReportBatchWorker
from app.reporting_jobs.models import ReportCallerContext


class BatchRuntimeLedger(Protocol):
    def list_runnable_batch_ids(
        self,
        *,
        tenant_id: str,
        limit: int = 10,
        now: datetime | None = None,
    ) -> list[str]: ...

    def batch_pressure_snapshot(self, *, now: datetime | None = None) -> BatchPressureSnapshot: ...


@dataclass(frozen=True)
class BatchRuntimePassResult:
    worker_id: str
    scanned_batch_ids: list[str] = field(default_factory=list)
    batch_results: list[BatchWorkerRunResult] = field(default_factory=list)
    recovered_count: int = 0
    leased_count: int = 0
    dispatched_count: int = 0
    executed_count: int = 0
    pressure_snapshot: BatchPressureSnapshot = field(default_factory=BatchPressureSnapshot)
    back_pressure_stopped: bool = False
    back_pressure_reasons: list[str] = field(default_factory=list)


class ReportBatchRuntime:
    """Bounded internal runtime pass over durable runnable batches.

    This is intentionally not a daemon or scheduler loop. It provides the
    production-shaped unit that a later process manager can invoke: scan a small
    ordered set of runnable batches from the durable ledger and advance each
    through the existing single-batch worker primitive.

    A pass is scoped to the tenant of the caller context it runs under, so a
    background worker only advances batches it is governed to act for. Running
    for more than one tenant means running one process per governed tenant.
    """

    def __init__(
        self,
        *,
        batch_ledger: BatchRuntimeLedger,
        worker: ReportBatchWorker,
    ) -> None:
        self._batch_ledger = batch_ledger
        self._worker = worker

    async def run_pass(
        self,
        *,
        caller_context: ReportCallerContext,
        worker_id: str,
        max_batches: int = 5,
        runtime_load: BatchRuntimeLoad | None = None,
        dispatch_policy: BatchDispatchPolicy | None = None,
        recover_expired_leases: bool = True,
        now: datetime | None = None,
    ) -> BatchRuntimePassResult:
        if max_batches < 1:
            return BatchRuntimePassResult(worker_id=worker_id)

        batch_ids = self._batch_ledger.list_runnable_batch_ids(
            tenant_id=caller_context.tenant_id,
            limit=max_batches,
            now=now,
        )
        batch_results: list[BatchWorkerRunResult] = []
        back_pressure_reasons: list[str] = []
        back_pressure_stopped = False

        for batch_id in batch_ids:
            result = await self._worker.run_once(
                batch_id=batch_id,
                caller_context=caller_context,
                worker_id=worker_id,
                runtime_load=runtime_load,
                dispatch_policy=dispatch_policy,
                recover_expired_leases=recover_expired_leases,
            )
            batch_results.append(result)
            if (
                result.back_pressure_reasons
                and result.dispatched_count == 0
                and result.executed_count == 0
            ):
                back_pressure_reasons = result.back_pressure_reasons
                back_pressure_stopped = True
                break

        return BatchRuntimePassResult(
            worker_id=worker_id,
            scanned_batch_ids=batch_ids,
            batch_results=batch_results,
            recovered_count=sum(result.recovered_count for result in batch_results),
            leased_count=sum(result.leased_count for result in batch_results),
            dispatched_count=sum(result.dispatched_count for result in batch_results),
            executed_count=sum(result.executed_count for result in batch_results),
            pressure_snapshot=self._batch_ledger.batch_pressure_snapshot(now=now),
            back_pressure_stopped=back_pressure_stopped,
            back_pressure_reasons=back_pressure_reasons,
        )
