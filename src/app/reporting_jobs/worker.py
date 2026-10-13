from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol

from app.reporting_document_format import document_output_format
from app.reporting_jobs.custody_policy import REQUIRED_POLICY, current_work_lease
from app.reporting_jobs.execution import ReportJobExecutionResult
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_jobs.work_queue import ReportJobWorkItem, ReportJobWorkRetryPolicy

TERMINAL_JOB_STATUSES = {"completed", "completed_with_warnings", "archived", "failed", "cancelled"}
WORK_ITEM_SECONDS = 60
WORK_PASS_SECONDS = 120


class ReportJobWorkLedger(Protocol):
    def claim_work_items(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        retry_policy: ReportJobWorkRetryPolicy | None = None,
    ) -> list[ReportJobWorkItem]: ...

    def complete_work_item(
        self,
        *,
        work_item_id: str,
        lease_token: str,
    ) -> ReportJobWorkItem: ...

    def defer_work_item(
        self,
        *,
        work_item_id: str,
        lease_token: str,
        wait_reason: str,
        delay_seconds: int,
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
    async def execute_job(self, *, job_id: str) -> ReportJobExecutionResult: ...


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
    """Share one allowance across work; hard DB bounds require provider support."""

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
        outcomes: list[ReportJobWorkOutcome] = []
        deadline = monotonic() + WORK_PASS_SECONDS
        for _ in range(min(5, max(0, max_items))):
            started_at = monotonic()
            if deadline - started_at < WORK_ITEM_SECONDS:
                break
            outcome = await self._run_item(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                deadline=min(deadline, started_at + WORK_ITEM_SECONDS),
            )
            if outcome is None:
                break
            outcomes.append(outcome)
        return ReportJobWorkerRunResult(
            worker_id=worker_id,
            claimed_count=len(outcomes),
            completed_count=sum(outcome.work_status == "completed" for outcome in outcomes),
            retry_pending_count=sum(outcome.work_status == "retry_pending" for outcome in outcomes),
            failed_count=sum(outcome.work_status == "failed" for outcome in outcomes),
            outcomes=outcomes,
        )

    async def _run_item(
        self, *, worker_id: str, lease_seconds: int, deadline: float
    ) -> ReportJobWorkOutcome | None:
        work: ReportJobWorkItem | None = None
        try:
            # One allowance starts before claim/sweep and includes settlement.
            # Async cancellation cannot interrupt a blocking DB operation. The
            # provider's absolute transaction/cleanup deadline remains a prerequisite.
            async with asyncio.timeout(max(0, deadline - monotonic())):
                _check_deadline(deadline)
                items = self._work_ledger.claim_work_items(
                    worker_id=worker_id,
                    limit=1,
                    lease_seconds=lease_seconds,
                    retry_policy=self._retry_policy,
                )
                if not items:
                    return None
                work = items[0]
                _check_deadline(deadline)
                return await self._execute_work_item(work, deadline)
        except TimeoutError:
            if work is None:
                return None
            # Do not start another transaction after expiry or pretend an
            # ambiguous settlement failed. Existing expiry recovery owns the row.
            return ReportJobWorkOutcome(
                work_item_id=work.work_item_id,
                report_job_id=work.report_job_id,
                work_status="unknown",
                job_status="unknown",
                failure_category="report_job_worker_deadline_expired",
            )

    async def _execute_work_item(
        self, work_item: ReportJobWorkItem, deadline: float
    ) -> ReportJobWorkOutcome:
        lease_token = work_item.lease_token
        if not lease_token:
            raise RuntimeError("report_job_work_item_missing_lease_token")
        try:
            execution = await self._execute_with_lease(work_item, lease_token)
        except Exception as exc:
            _check_deadline(deadline)
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

        _check_deadline(deadline)
        job = execution.job
        if job.custody_policy == REQUIRED_POLICY and job.custody_state in {"blocked", "refused"}:
            return ReportJobWorkOutcome(
                work_item_id=work_item.work_item_id,
                report_job_id=work_item.report_job_id,
                work_status="failed",
                job_status=job.status,
                failure_category="custody_parked",
            )
        if execution.waiting_on_owner:
            # The EXPLICIT waiting outcome from execution: whatever recovery
            # status the job holds (rendering, completed, archiving), DEFER
            # without burning the failure budget - the eventual owner
            # outcome is adopted under the same render id, and stale
            # escalation is the owner's diagnostics contract, not a local
            # poll count (report#303).
            deferred_work = self._work_ledger.defer_work_item(
                work_item_id=work_item.work_item_id,
                lease_token=lease_token,
                wait_reason="waiting_on_render",
                delay_seconds=self._retry_policy.base_delay_seconds,
            )
            return ReportJobWorkOutcome(
                work_item_id=work_item.work_item_id,
                report_job_id=work_item.report_job_id,
                work_status=deferred_work.status,
                job_status=job.status,
                failure_category="waiting_on_render",
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

    async def _execute_with_lease(
        self, work_item: ReportJobWorkItem, token: str
    ) -> ReportJobExecutionResult:
        binding = current_work_lease.set((work_item.work_item_id, token))
        try:
            return await self._execution_service.execute_job(job_id=work_item.report_job_id)
        finally:
            current_work_lease.reset(binding)


def _check_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise TimeoutError("report_job_worker_deadline_expired")


def _is_terminal_job(job: ReportJobLedgerRecord) -> bool:
    if job.status in TERMINAL_JOB_STATUSES:
        return True
    return (
        job.status == "data_ready" and document_output_format(job.requested_output_formats) is None
    )
