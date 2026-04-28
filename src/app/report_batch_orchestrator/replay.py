from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.report_batch_orchestrator.models import (
    BatchItemReplayRequest,
    BatchRetryPolicy,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.service import get_report_batch_ledger
from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobLedgerRecord,
    ReportStatusEvent,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_render.replay_service import (
    append_source_event_once,
    assert_replay_eligible,
)


@dataclass(frozen=True)
class BatchItemReplayResult:
    batch_id: str
    item: ReportBatchItemRecord
    source_report_job: ReportJobLedgerRecord
    replayed_report_job: ReportJobLedgerRecord
    idempotency_key: str


class BatchReplayLedger(Protocol):
    def get_batch(self, batch_id: str) -> ReportBatchRecord: ...

    def get_batch_item(self, batch_id: str, batch_item_id: str) -> ReportBatchItemRecord: ...

    def relink_failed_item_for_replay(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
        replayed_report_job_id: str,
        retry_policy: BatchRetryPolicy | None = None,
    ) -> ReportBatchItemRecord: ...


class BatchReplayReportJobLedger(Protocol):
    def get_job(self, job_id: str) -> ReportJobLedgerRecord: ...

    def create_portfolio_review_job(
        self,
        *,
        request: PortfolioReviewJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportJobLedgerRecord: ...

    def append_job_event(
        self,
        *,
        job_id: str,
        event_type: str,
        message: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> None: ...

    def list_status_events(self, job_id: str) -> list[ReportStatusEvent]: ...


class ReportBatchItemReplayService:
    def __init__(
        self,
        *,
        batch_ledger: BatchReplayLedger,
        report_job_ledger: BatchReplayReportJobLedger,
    ) -> None:
        self._batch_ledger = batch_ledger
        self._report_job_ledger = report_job_ledger

    def replay_item(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
        command: BatchItemReplayRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> BatchItemReplayResult:
        batch = self._batch_ledger.get_batch(batch_id)
        item = self._batch_ledger.get_batch_item(batch_id, batch_item_id)
        replay_key = _batch_item_replay_idempotency_key(
            batch_item_id=batch_item_id,
            idempotency_key=idempotency_key,
        )
        if item.status == "waiting_on_report_job" and item.report_job_id:
            replayed_job = self._report_job_ledger.get_job(item.report_job_id)
            if replayed_job.idempotency_key != replay_key:
                raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")
            source_job = self._source_job_for_replayed_job(replayed_job)
            return BatchItemReplayResult(
                batch_id=batch_id,
                item=item,
                source_report_job=source_job,
                replayed_report_job=replayed_job,
                idempotency_key=idempotency_key or "",
            )

        source_job = self._source_job_for_item(item)
        assert_replay_eligible(source_job)
        replayed_job = self._report_job_ledger.create_portfolio_review_job(
            request=self._request_for_item(batch=batch, item=item),
            caller_context=caller_context,
            idempotency_key=replay_key,
        )
        replayed_item = self._batch_ledger.relink_failed_item_for_replay(
            batch_id=batch_id,
            batch_item_id=batch_item_id,
            replayed_report_job_id=replayed_job.job_id,
        )
        append_source_event_once(
            self._report_job_ledger,
            job_id=source_job.job_id,
            event_type="batch_item_replay_requested",
            replayed_job_id=replayed_job.job_id,
            message=(
                f"Batch item {batch_item_id} replay requested as {replayed_job.job_id}: "
                f"{command.reason}"
            ),
            caller_context=caller_context,
        )
        append_source_event_once(
            self._report_job_ledger,
            job_id=replayed_job.job_id,
            event_type="batch_item_replay_lineage_bound",
            replayed_job_id=source_job.job_id,
            message=f"Batch item replay source job {source_job.job_id}.",
            caller_context=caller_context,
        )
        return BatchItemReplayResult(
            batch_id=batch_id,
            item=replayed_item,
            source_report_job=source_job,
            replayed_report_job=replayed_job,
            idempotency_key=idempotency_key or "",
        )

    def _source_job_for_item(self, item: ReportBatchItemRecord) -> ReportJobLedgerRecord:
        if item.status != "failed_retryable" or not item.retry_eligible:
            raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")
        if not item.report_job_id:
            raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")
        return self._report_job_ledger.get_job(item.report_job_id)

    def _source_job_for_replayed_job(
        self, replayed_job: ReportJobLedgerRecord
    ) -> ReportJobLedgerRecord:
        for event in self._report_job_ledger.list_status_events(replayed_job.job_id):
            prefix = "Batch item replay source job "
            message = event.message or ""
            if event.event_type == "batch_item_replay_lineage_bound" and message.startswith(prefix):
                source_job_id = message.removeprefix(prefix).rstrip(".")
                return self._report_job_ledger.get_job(source_job_id)
        raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")

    def _request_for_item(
        self,
        *,
        batch: ReportBatchRecord,
        item: ReportBatchItemRecord,
    ) -> PortfolioReviewJobRequest:
        return PortfolioReviewJobRequest(
            portfolio_scope={"portfolio_ids": [item.portfolio_id]},
            as_of_date=batch.as_of_date,
            requested_output_formats=batch.requested_output_formats,
            reporting_currency=batch.reporting_currency,
            options=batch.options,
        )


def get_report_batch_item_replay_service() -> ReportBatchItemReplayService:
    return ReportBatchItemReplayService(
        batch_ledger=get_report_batch_ledger(),
        report_job_ledger=get_report_job_ledger(),
    )


def _batch_item_replay_idempotency_key(
    *,
    batch_item_id: str,
    idempotency_key: str | None,
) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise MissingIdempotencyKeyError("missing_idempotency_key")
    return f"batch-item-replay:{batch_item_id}:{idempotency_key.strip()}"
