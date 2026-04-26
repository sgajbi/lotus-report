from __future__ import annotations

from typing import Protocol

from app.report_batch_orchestrator.models import (
    BatchDispatchPolicy,
    BatchDispatchResult,
    BatchRuntimeLoad,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobLedgerRecord,
)


class BatchLedger(Protocol):
    def get_batch(self, batch_id: str) -> ReportBatchRecord: ...

    def count_active_batches(self) -> int: ...

    def count_active_items(self) -> int: ...

    def acquire_dispatch_items(
        self,
        *,
        batch_id: str,
        worker_id: str,
        lease_seconds: int,
        limit: int,
    ) -> list[ReportBatchItemRecord]: ...

    def mark_item_waiting_on_report_job(
        self,
        *,
        batch_item_id: str,
        lease_token: str,
        report_job_id: str,
    ) -> ReportBatchItemRecord: ...


class ReportJobLedger(Protocol):
    def create_portfolio_review_job(
        self,
        *,
        request: PortfolioReviewJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportJobLedgerRecord: ...


class ReportBatchDispatcher:
    """Dispatch durable batch items into report jobs under explicit runtime limits."""

    def __init__(
        self,
        *,
        batch_ledger: BatchLedger,
        report_job_ledger: ReportJobLedger,
        policy: BatchDispatchPolicy | None = None,
    ):
        self._batch_ledger = batch_ledger
        self._report_job_ledger = report_job_ledger
        self._policy = policy or BatchDispatchPolicy()

    def dispatch_batch(
        self,
        *,
        batch_id: str,
        caller_context: ReportCallerContext,
        worker_id: str,
        runtime_load: BatchRuntimeLoad | None = None,
    ) -> BatchDispatchResult:
        batch = self._batch_ledger.get_batch(batch_id)
        load = self._runtime_load_for_batch(batch=batch, runtime_load=runtime_load)
        back_pressure_reasons = evaluate_back_pressure(
            load,
            self._policy,
        )
        if back_pressure_reasons:
            return BatchDispatchResult(
                batch_id=batch_id,
                leased_count=0,
                dispatched_count=0,
                report_job_ids=[],
                back_pressure_reasons=back_pressure_reasons,
            )

        dispatch_capacity = self._policy.max_active_items - load.active_items
        if dispatch_capacity < 1:
            return BatchDispatchResult(
                batch_id=batch_id,
                leased_count=0,
                dispatched_count=0,
                report_job_ids=[],
                back_pressure_reasons=["max_active_items"],
            )
        leased_items = self._batch_ledger.acquire_dispatch_items(
            batch_id=batch_id,
            worker_id=worker_id,
            lease_seconds=self._policy.lease_seconds,
            limit=min(self._policy.max_active_items, dispatch_capacity),
        )
        report_job_ids: list[str] = []
        for item in leased_items:
            if item.lease_token is None:
                raise RuntimeError("batch_item_missing_lease_token")
            record = self._report_job_ledger.create_portfolio_review_job(
                request=PortfolioReviewJobRequest(
                    portfolio_scope={"portfolio_ids": [item.portfolio_id]},
                    as_of_date=batch.as_of_date,
                    requested_output_formats=batch.requested_output_formats,
                    reporting_currency=batch.reporting_currency,
                    options=batch.options,
                ),
                caller_context=caller_context,
                idempotency_key=item.item_idempotency_key,
            )
            self._batch_ledger.mark_item_waiting_on_report_job(
                batch_item_id=item.batch_item_id,
                lease_token=item.lease_token,
                report_job_id=record.job_id,
            )
            report_job_ids.append(record.job_id)

        return BatchDispatchResult(
            batch_id=batch_id,
            leased_count=len(leased_items),
            dispatched_count=len(report_job_ids),
            report_job_ids=report_job_ids,
            back_pressure_reasons=[],
        )

    def _runtime_load_for_batch(
        self,
        *,
        batch: ReportBatchRecord,
        runtime_load: BatchRuntimeLoad | None,
    ) -> BatchRuntimeLoad:
        external_load = runtime_load or BatchRuntimeLoad()
        durable_active_batches = self._batch_ledger.count_active_batches()
        if batch.status == "running":
            durable_active_batches = max(0, durable_active_batches - 1)
        return BatchRuntimeLoad(
            active_batches=durable_active_batches + external_load.active_batches,
            active_items=self._batch_ledger.count_active_items() + external_load.active_items,
            active_upstream_jobs=external_load.active_upstream_jobs,
            active_render_jobs=external_load.active_render_jobs,
            active_archive_jobs=external_load.active_archive_jobs,
        )


def evaluate_back_pressure(
    runtime_load: BatchRuntimeLoad,
    policy: BatchDispatchPolicy,
) -> list[str]:
    reasons: list[str] = []
    if runtime_load.active_batches >= policy.max_active_batches:
        reasons.append("max_active_batches_reached")
    if runtime_load.active_items >= policy.max_active_items:
        reasons.append("max_active_items_reached")
    if runtime_load.active_upstream_jobs >= policy.max_active_upstream_jobs:
        reasons.append("max_active_upstream_jobs_reached")
    if runtime_load.active_render_jobs >= policy.max_active_render_jobs:
        reasons.append("max_active_render_jobs_reached")
    if runtime_load.active_archive_jobs >= policy.max_active_archive_jobs:
        reasons.append("max_active_archive_jobs_reached")
    return reasons
