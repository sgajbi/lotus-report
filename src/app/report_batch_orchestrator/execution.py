from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from app.report_batch_orchestrator.models import (
    BatchRetryPolicy,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.reporting_jobs.execution import (
    ReportJobExecutionLedger,
    ReportJobExecutionService,
    ReportRenderOrchestrationService,
    ReportSnapshotCaptureService,
)
from app.reporting_jobs.ledger import ReportJobNotFoundError
from app.reporting_jobs.models import ReportJobLedgerRecord

# Metrics are recorded at the boundary (worker process, run-once route), never here:
# app.reporting_metrics imports this package's models, so importing it back from a module
# that report_batch_orchestrator/__init__.py loads would be a circular import.
TENANT_MISMATCH_CATEGORY = "batch_item_tenant_mismatch"
MISSING_LINKED_JOB_CATEGORY = "batch_item_report_job_missing"


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
        logger: logging.Logger | None = None,
    ) -> None:
        self._batch_ledger = batch_ledger
        self._report_job_ledger = report_job_ledger
        self._job_execution_service = ReportJobExecutionService(
            report_job_ledger=report_job_ledger,
            capture_service=capture_service,
            render_service=render_service,
        )
        self._retry_policy = retry_policy or BatchRetryPolicy()
        self._logger = logger or logging.getLogger("report_batch_execution")

    async def execute_item(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
    ) -> BatchItemExecutionResult:
        batch, item = self._load_waiting_item(batch_id=batch_id, batch_item_id=batch_item_id)
        if item.report_job_id is None:
            raise ValueError("batch_item_report_job_missing")

        quarantined = self._quarantine_unusable_linked_job(batch=batch, item=item)
        if quarantined is not None:
            return quarantined

        try:
            job = await self._job_execution_service.execute_job(job_id=item.report_job_id)
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

    def _load_waiting_item(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
    ) -> tuple[ReportBatchRecord, ReportBatchItemRecord]:
        batch = self._batch_ledger.get_batch(batch_id)
        for item in batch.items:
            if item.batch_item_id == batch_item_id:
                if item.status != "waiting_on_report_job":
                    raise ValueError("batch_item_not_waiting_on_report_job")
                return batch, item
        raise ValueError("report_batch_item_not_found")

    def _quarantine_unusable_linked_job(
        self,
        *,
        batch: ReportBatchRecord,
        item: ReportBatchItemRecord,
    ) -> BatchItemExecutionResult | None:
        """Refuse to execute a linked report job that is foreign or absent.

        Batch admission fences the batch record, but the link from item to report job was
        created by whichever worker dispatched it. A link created before dispatch was
        tenant-scoped can point at another tenant's job, and no admission check on the batch
        can see that, because the mismatch is on the far side of the link.

        `report_batch_item.report_job_id` carries no foreign key - report jobs live in a
        separate ledger, so one is not even expressible - and the lookup runs before the
        execution `try`. An absent job would therefore raise straight out of `execute_item`,
        take down the whole worker pass, and kill the next pass on the same row: one broken
        link would stop every tenant's batches advancing. Both faults are durable data
        defects rather than transient failures, so both are terminal here. Routing them to
        the execution handler instead would mark them `retryable=True` and reproduce the
        same loop more slowly.

        Only `ReportJobNotFoundError` means an absent row. A connection or query fault must
        not be read as a dangling link: quarantine is permanent, so misclassifying a brief
        report-ledger outage would terminally fail every waiting item whose batch-ledger
        write then succeeded - a wider outage than the stall this lookup prevents. Any other
        exception is recorded `retryable=True` with the same category the execution handler
        uses, because that is what a transient fault is.

        This is a background path with no caller to disclose to, so both are loud rather
        than opaque: quarantined as terminally failed, never retried, resolved by a human.
        """

        report_job_id = item.report_job_id
        if report_job_id is None:
            return None
        try:
            job = self._report_job_ledger.get_job(report_job_id)
        except ReportJobNotFoundError:
            return self._quarantine(
                batch=batch,
                item=item,
                report_job_id=report_job_id,
                category=MISSING_LINKED_JOB_CATEGORY,
                summary=(
                    "Linked report job does not exist; execution refused and the item "
                    "quarantined for operator review."
                ),
            )
        except Exception as exc:
            return self._retryable_failure(
                batch=batch,
                item=item,
                report_job_id=report_job_id,
                summary=str(exc) or exc.__class__.__name__,
            )
        if job.tenant_id == batch.tenant_id:
            return None
        return self._quarantine(
            batch=batch,
            item=item,
            report_job_id=report_job_id,
            category=TENANT_MISMATCH_CATEGORY,
            summary=(
                "Linked report job belongs to a different tenant than its batch; "
                "execution refused and the item quarantined for operator review."
            ),
        )

    def _retryable_failure(
        self,
        *,
        batch: ReportBatchRecord,
        item: ReportBatchItemRecord,
        report_job_id: str,
        summary: str,
    ) -> BatchItemExecutionResult:
        """Record a transient fault the way the execution handler does: retryable."""

        failed_item = self._batch_ledger.mark_item_failed(
            batch_item_id=item.batch_item_id,
            error_category="batch_execution_failed",
            error_summary=summary,
            retryable=True,
            retry_policy=self._retry_policy,
        )
        return BatchItemExecutionResult(
            batch_id=batch.batch_id,
            batch_item_id=item.batch_item_id,
            report_job_id=report_job_id,
            item_status=failed_item.status,
            report_job_status="unknown",
            failure_category=failed_item.last_error_category,
            retry_eligible=failed_item.retry_eligible,
        )

    def _quarantine(
        self,
        *,
        batch: ReportBatchRecord,
        item: ReportBatchItemRecord,
        report_job_id: str,
        category: str,
        summary: str,
    ) -> BatchItemExecutionResult:
        self._logger.error(
            category,
            extra={
                "extra_fields": {
                    "batch_id": batch.batch_id,
                    "batch_item_id": item.batch_item_id,
                    "report_job_id": report_job_id,
                    "failure_category": category,
                }
            },
        )
        quarantined = self._batch_ledger.mark_item_failed(
            batch_item_id=item.batch_item_id,
            error_category=category,
            error_summary=summary,
            retryable=False,
            retry_policy=self._retry_policy,
        )
        return BatchItemExecutionResult(
            batch_id=batch.batch_id,
            batch_item_id=item.batch_item_id,
            report_job_id=report_job_id,
            item_status=quarantined.status,
            report_job_status="not_executed",
            failure_category=quarantined.last_error_category,
            retry_eligible=quarantined.retry_eligible,
        )


def _is_successful_job(job: ReportJobLedgerRecord) -> bool:
    if job.status in {"completed", "completed_with_warnings", "archived"}:
        return True
    return job.status == "data_ready" and "pdf" not in job.requested_output_formats
