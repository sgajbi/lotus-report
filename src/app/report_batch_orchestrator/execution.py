from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.report_batch_orchestrator.models import (
    BatchRetryPolicy,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.reporting_jobs.models import ReportJobLedgerRecord


class BatchExecutionLedger(Protocol):
    def get_batch(self, batch_id: str) -> ReportBatchRecord: ...

    def mark_item_succeeded(
        self,
        *,
        batch_item_id: str,
        report_job_id: str,
    ) -> ReportBatchItemRecord: ...

    def mark_item_failed(
        self,
        *,
        batch_item_id: str,
        error_category: str,
        error_summary: str,
        retryable: bool,
        retry_policy: BatchRetryPolicy | None = None,
    ) -> ReportBatchItemRecord: ...


class ReportJobExecutionLedger(Protocol):
    def get_job(self, job_id: str) -> ReportJobLedgerRecord: ...


class ReportSnapshotCaptureService(Protocol):
    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


class ReportRenderOrchestrationService(Protocol):
    async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


@dataclass(frozen=True)
class BatchItemExecutionResult:
    batch_id: str
    batch_item_id: str
    report_job_id: str
    item_status: str
    report_job_status: str
    failure_category: str | None = None
    retry_eligible: bool = False


class ReportBatchExecutionService:
    """Advance a dispatched batch item through the existing report-job pipeline."""

    def __init__(
        self,
        *,
        batch_ledger: BatchExecutionLedger,
        report_job_ledger: ReportJobExecutionLedger,
        capture_service: ReportSnapshotCaptureService,
        render_service: ReportRenderOrchestrationService,
        retry_policy: BatchRetryPolicy | None = None,
    ) -> None:
        self._batch_ledger = batch_ledger
        self._report_job_ledger = report_job_ledger
        self._capture_service = capture_service
        self._render_service = render_service
        self._retry_policy = retry_policy or BatchRetryPolicy()

    async def execute_item(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
    ) -> BatchItemExecutionResult:
        item = self._load_waiting_item(batch_id=batch_id, batch_item_id=batch_item_id)
        if item.report_job_id is None:
            raise ValueError("batch_item_report_job_missing")

        try:
            job = self._report_job_ledger.get_job(item.report_job_id)
            if job.status == "accepted":
                job = await self._capture_service.capture_for_job(job)
            if job.status == "data_ready" and "pdf" in job.requested_output_formats:
                job = await self._render_service.render_for_job(job)
        except Exception as exc:
            failed_item = self._batch_ledger.mark_item_failed(
                batch_item_id=item.batch_item_id,
                error_category="batch_execution_failed",
                error_summary=str(exc) or exc.__class__.__name__,
                retryable=True,
                retry_policy=self._retry_policy,
            )
            return BatchItemExecutionResult(
                batch_id=batch_id,
                batch_item_id=batch_item_id,
                report_job_id=item.report_job_id,
                item_status=failed_item.status,
                report_job_status="unknown",
                failure_category=failed_item.last_error_category,
                retry_eligible=failed_item.retry_eligible,
            )

        if _is_successful_job(job):
            completed_item = self._batch_ledger.mark_item_succeeded(
                batch_item_id=item.batch_item_id,
                report_job_id=job.job_id,
            )
            return BatchItemExecutionResult(
                batch_id=batch_id,
                batch_item_id=batch_item_id,
                report_job_id=job.job_id,
                item_status=completed_item.status,
                report_job_status=job.status,
            )

        if job.status == "failed":
            failed_item = self._batch_ledger.mark_item_failed(
                batch_item_id=item.batch_item_id,
                error_category=job.failure_category or "report_job_failed",
                error_summary=job.failure_message or "Report job failed.",
                retryable=job.retry_eligible,
                retry_policy=self._retry_policy,
            )
            return BatchItemExecutionResult(
                batch_id=batch_id,
                batch_item_id=batch_item_id,
                report_job_id=job.job_id,
                item_status=failed_item.status,
                report_job_status=job.status,
                failure_category=failed_item.last_error_category,
                retry_eligible=failed_item.retry_eligible,
            )

        return BatchItemExecutionResult(
            batch_id=batch_id,
            batch_item_id=batch_item_id,
            report_job_id=job.job_id,
            item_status=item.status,
            report_job_status=job.status,
        )

    def _load_waiting_item(self, *, batch_id: str, batch_item_id: str) -> ReportBatchItemRecord:
        batch = self._batch_ledger.get_batch(batch_id)
        for item in batch.items:
            if item.batch_item_id == batch_item_id:
                if item.status != "waiting_on_report_job":
                    raise ValueError("batch_item_not_waiting_on_report_job")
                return item
        raise ValueError("report_batch_item_not_found")


def _is_successful_job(job: ReportJobLedgerRecord) -> bool:
    if job.status in {"completed", "completed_with_warnings", "archived"}:
        return True
    return job.status == "data_ready" and "pdf" not in job.requested_output_formats
