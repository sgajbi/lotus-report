from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_jobs.work_queue import ReportJobWorkItem, ReportJobWorkRetryPolicy

TERMINAL_JOB_STATUSES = {"completed", "completed_with_warnings", "archived", "failed", "cancelled"}


class ReportJobWorkLedger(Protocol):
    def claim_work_items(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[ReportJobWorkItem]: ...

    def complete_work_item(
        self,
        *,
        work_item_id: str,
        lease_token: str,
    ) -> ReportJobWorkItem: ...

    def fail_work_item(
        self,
        *,
        work_item_id: str,
        lease_token: str,
        error_category: str,
        error_summary: str,
        retry_policy: ReportJobWorkRetryPolicy | None = None,
    ) -> ReportJobWorkItem: ...


class ReportJobExecutor(Protocol):
    async def execute_job(self, *, job_id: str) -> ReportJobLedgerRecord: ...


@dataclass(frozen=True)
class ReportJobWorkOutcome:
    work_item_id: str
    report_job_id: str
    work_status: str
    job_status: str
    failure_category: str | None = None


@dataclass(frozen=True)
class ReportJobWorkerRunResult:
    worker_id: str
    claimed_count: int
    completed_count: int
    retry_pending_count: int
    failed_count: int
    outcomes: list[ReportJobWorkOutcome] = field(default_factory=list)


class ReportJobWorker:
    """Run one bounded pass over durable interactive report-job work."""

    def __init__(
        self,
        *,
        work_ledger: ReportJobWorkLedger,
        execution_service: ReportJobExecutor,
        retry_policy: ReportJobWorkRetryPolicy | None = None,
    ) -> None:
        self._work_ledger = work_ledger
        self._execution_service = execution_service
        self._retry_policy = retry_policy or ReportJobWorkRetryPolicy()

    async def run_once(
        self,
        *,
        worker_id: str,
        max_items: int,
        lease_seconds: int,
    ) -> ReportJobWorkerRunResult:
        work_items = self._work_ledger.claim_work_items(
            worker_id=worker_id,
            limit=max_items,
            lease_seconds=lease_seconds,
        )
        outcomes: list[ReportJobWorkOutcome] = []
        for work_item in work_items:
            outcomes.append(await self._execute_work_item(work_item))
        return ReportJobWorkerRunResult(
            worker_id=worker_id,
            claimed_count=len(work_items),
            completed_count=sum(outcome.work_status == "completed" for outcome in outcomes),
            retry_pending_count=sum(outcome.work_status == "retry_pending" for outcome in outcomes),
            failed_count=sum(outcome.work_status == "failed" for outcome in outcomes),
            outcomes=outcomes,
        )

    async def _execute_work_item(self, work_item: ReportJobWorkItem) -> ReportJobWorkOutcome:
        lease_token = work_item.lease_token
        if not lease_token:
            raise RuntimeError("report_job_work_item_missing_lease_token")
        try:
            job = await self._execution_service.execute_job(job_id=work_item.report_job_id)
        except Exception as exc:
            failed_work = self._work_ledger.fail_work_item(
                work_item_id=work_item.work_item_id,
                lease_token=lease_token,
                error_category="report_job_worker_execution_failed",
                error_summary=str(exc) or exc.__class__.__name__,
                retry_policy=self._retry_policy,
            )
            return ReportJobWorkOutcome(
                work_item_id=work_item.work_item_id,
                report_job_id=work_item.report_job_id,
                work_status=failed_work.status,
                job_status="unknown",
                failure_category=failed_work.last_error_category,
            )

        if not _is_terminal_job(job):
            failed_work = self._work_ledger.fail_work_item(
                work_item_id=work_item.work_item_id,
                lease_token=lease_token,
                error_category="report_job_worker_incomplete",
                error_summary=f"Report job remained in non-terminal state {job.status}.",
                retry_policy=self._retry_policy,
            )
            return ReportJobWorkOutcome(
                work_item_id=work_item.work_item_id,
                report_job_id=work_item.report_job_id,
                work_status=failed_work.status,
                job_status=job.status,
                failure_category=failed_work.last_error_category,
            )

        completed_work = self._work_ledger.complete_work_item(
            work_item_id=work_item.work_item_id,
            lease_token=lease_token,
        )
        return ReportJobWorkOutcome(
            work_item_id=work_item.work_item_id,
            report_job_id=work_item.report_job_id,
            work_status=completed_work.status,
            job_status=job.status,
            failure_category=job.failure_category,
        )


def _is_terminal_job(job: ReportJobLedgerRecord) -> bool:
    if job.status in TERMINAL_JOB_STATUSES:
        return True
    return job.status == "data_ready" and "pdf" not in job.requested_output_formats
