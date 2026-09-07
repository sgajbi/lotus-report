from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher
from app.report_batch_orchestrator.execution import BatchItemExecutionResult
from app.report_batch_orchestrator.models import (
    BatchDispatchPolicy,
    BatchRecoveryResult,
    BatchRuntimeLoad,
    BatchStatus,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.tenant_admission import admit_batch
from app.reporting_jobs.models import ReportCallerContext

RUNNABLE_BATCH_STATUSES = {"materialized", "running"}
WAITING_ITEM_STATUS = "waiting_on_report_job"


class BatchWorkerLedger(Protocol):
    def get_batch(self, batch_id: str) -> ReportBatchRecord: ...

    def recover_expired_leases(self, *, batch_id: str) -> BatchRecoveryResult: ...


class BatchItemExecutor(Protocol):
    async def execute_item(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
    ) -> BatchItemExecutionResult: ...


@dataclass(frozen=True)
class BatchWorkerRunResult:
    batch_id: str
    batch_status_before: BatchStatus
    batch_status_after: BatchStatus
    recovered_count: int
    leased_count: int
    dispatched_count: int
    executed_count: int
    report_job_ids: list[str] = field(default_factory=list)
    back_pressure_reasons: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    execution_results: list[BatchItemExecutionResult] = field(default_factory=list)


class ReportBatchWorker:
    """Bounded internal worker run for one durable report batch.

    The production scheduler is intentionally outside this class. This primitive
    gives schedulers, operators, and tests one deterministic unit of work:
    recover expired pre-dispatch leases, dispatch eligible items under back
    pressure, then advance waiting report jobs through the execution bridge.
    """

    def __init__(
        self,
        *,
        batch_ledger: BatchWorkerLedger,
        dispatcher: ReportBatchDispatcher,
        execution_service: BatchItemExecutor,
    ) -> None:
        self._batch_ledger = batch_ledger
        self._dispatcher = dispatcher
        self._execution_service = execution_service

    async def run_once(
        self,
        *,
        batch_id: str,
        caller_context: ReportCallerContext,
        worker_id: str,
        runtime_load: BatchRuntimeLoad | None = None,
        dispatch_policy: BatchDispatchPolicy | None = None,
        recover_expired_leases: bool = True,
    ) -> BatchWorkerRunResult:
        before = admit_batch(
            self._batch_ledger.get_batch(batch_id),
            caller_context=caller_context,
        )
        if before.status not in RUNNABLE_BATCH_STATUSES:
            return BatchWorkerRunResult(
                batch_id=batch_id,
                batch_status_before=before.status,
                batch_status_after=before.status,
                recovered_count=0,
                leased_count=0,
                dispatched_count=0,
                executed_count=0,
                skipped_reason=f"batch_not_runnable:{before.status}",
            )

        recovery = (
            self._batch_ledger.recover_expired_leases(batch_id=batch_id)
            if recover_expired_leases
            else BatchRecoveryResult(
                batch_id=batch_id,
                recovered_count=0,
                recovery_pending_item_ids=[],
            )
        )
        dispatch = self._dispatcher.dispatch_batch(
            batch_id=batch_id,
            caller_context=caller_context,
            worker_id=worker_id,
            runtime_load=runtime_load,
            policy=dispatch_policy,
        )
        execution_results = await self._execute_waiting_items(batch_id=batch_id)
        after = self._batch_ledger.get_batch(batch_id)

        return BatchWorkerRunResult(
            batch_id=batch_id,
            batch_status_before=before.status,
            batch_status_after=after.status,
            recovered_count=recovery.recovered_count,
            leased_count=dispatch.leased_count,
            dispatched_count=dispatch.dispatched_count,
            executed_count=len(execution_results),
            report_job_ids=dispatch.report_job_ids,
            back_pressure_reasons=dispatch.back_pressure_reasons,
            execution_results=execution_results,
        )

    async def _execute_waiting_items(self, *, batch_id: str) -> list[BatchItemExecutionResult]:
        batch = self._batch_ledger.get_batch(batch_id)
        waiting_item_ids = [
            item.batch_item_id
            for item in batch.items
            if item.status == WAITING_ITEM_STATUS and item.report_job_id is not None
        ]
        results: list[BatchItemExecutionResult] = []
        for batch_item_id in waiting_item_ids:
            results.append(
                await self._execution_service.execute_item(
                    batch_id=batch_id,
                    batch_item_id=batch_item_id,
                )
            )
        return results
