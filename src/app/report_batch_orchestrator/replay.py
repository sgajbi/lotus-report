from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from app.report_batch_orchestrator.execution import TENANT_MISMATCH_CATEGORY
from app.report_batch_orchestrator.models import (
    BatchItemReplayRequest,
    BatchRetryPolicy,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.service import get_report_batch_ledger
from app.report_batch_orchestrator.tenant_admission import admit_batch
from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobLedgerRecord,
    ReportJobRelationshipRecord,
    ReportJobRelationshipType,
    ReportStatusEvent,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_render.replay_service import (
    append_source_event_once,
    assert_replay_eligible,
)

# The two branches replay_item can take. _replay_branch_for is the single predicate: branch
# selection, the source-job loader, and the foreign-link quarantine all derive from it, so a
# later narrowing cannot leave one of them acting on a state the others no longer do.
REPLAY_BRANCH_IDEMPOTENT = "idempotent_replay"
REPLAY_BRANCH_NEW = "new_replay"


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

    def mark_item_failed(
        self,
        *,
        batch_item_id: str,
        error_category: str,
        error_summary: str,
        retryable: bool,
        retry_policy: BatchRetryPolicy | None = None,
    ) -> ReportBatchItemRecord: ...

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
        event_payload: dict[str, object] | None = None,
        event_idempotency_key: str | None = None,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> None: ...

    def list_status_events(self, job_id: str) -> list[ReportStatusEvent]: ...

    def upsert_job_relationship(
        self,
        *,
        source_job: ReportJobLedgerRecord,
        derived_job: ReportJobLedgerRecord,
        relationship_type: ReportJobRelationshipType,
        actor: str,
        reason: str,
        archive_consequence: str | None = None,
        previous_archive_document_id: str | None = None,
        new_archive_document_id: str | None = None,
    ) -> ReportJobRelationshipRecord: ...


class ReportBatchItemReplayService:
    def __init__(
        self,
        *,
        batch_ledger: BatchReplayLedger,
        report_job_ledger: BatchReplayReportJobLedger,
        logger: logging.Logger | None = None,
    ) -> None:
        self._batch_ledger = batch_ledger
        self._report_job_ledger = report_job_ledger
        self._logger = logger or logging.getLogger("report_batch_replay")

    def replay_item(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
        command: BatchItemReplayRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> BatchItemReplayResult:
        batch = admit_batch(
            self._batch_ledger.get_batch(batch_id),
            caller_context=caller_context,
        )
        item = self._batch_ledger.get_batch_item(batch_id, batch_item_id)
        # Establish the request is well-formed before anything can mutate durable state: a
        # missing Idempotency-Key must not be able to quarantine an item.
        replay_key = _batch_item_replay_idempotency_key(
            batch_item_id=batch_item_id,
            idempotency_key=idempotency_key,
        )
        branch = self._replay_branch_for(item)
        self._refuse_foreign_linked_job(batch=batch, item=item, branch=branch)
        if branch == REPLAY_BRANCH_IDEMPOTENT and item.report_job_id:
            replayed_job = self._report_job_ledger.get_job(item.report_job_id)
            if replayed_job.idempotency_key != replay_key:
                raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")
            source_job = self._source_job_for_replayed_job(batch=batch, replayed_job=replayed_job)
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
        self._report_job_ledger.upsert_job_relationship(
            source_job=source_job,
            derived_job=replayed_job,
            relationship_type="batch_item_replay",
            actor=caller_context.triggered_by,
            reason=command.reason,
            new_archive_document_id=replayed_job.archive_document_id,
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
            replayed_status=replayed_job.status,
            message=(
                f"Batch item {batch_item_id} replay requested as {replayed_job.job_id}: "
                f"{command.reason}"
            ),
            event_payload={"batch_item_id": batch_item_id},
            caller_context=caller_context,
        )
        append_source_event_once(
            self._report_job_ledger,
            job_id=replayed_job.job_id,
            event_type="batch_item_replay_lineage_bound",
            replayed_job_id=source_job.job_id,
            replayed_status=source_job.status,
            message=f"Batch item replay source job {source_job.job_id}.",
            event_payload={"batch_item_id": batch_item_id, "source_job_id": source_job.job_id},
            caller_context=caller_context,
        )
        return BatchItemReplayResult(
            batch_id=batch_id,
            item=replayed_item,
            source_report_job=source_job,
            replayed_report_job=replayed_job,
            idempotency_key=idempotency_key or "",
        )

    @staticmethod
    def _replay_branch_for(item: ReportBatchItemRecord) -> str | None:
        """Which replay branch would act on this item, or None if replay would not act.

        Single source of truth. Branch selection, `_source_job_for_item` and the foreign-link
        quarantine all read this, so narrowing replay eligibility cannot leave the quarantine
        mutating a state replay no longer touches.
        """

        if not item.report_job_id:
            return None
        if item.status == "waiting_on_report_job":
            return REPLAY_BRANCH_IDEMPOTENT
        if item.status == "failed_retryable" and item.retry_eligible:
            return REPLAY_BRANCH_NEW
        return None

    def _refuse_foreign_linked_job(
        self,
        *,
        batch: ReportBatchRecord,
        item: ReportBatchItemRecord,
        branch: str | None,
    ) -> None:
        """Refuse to replay an item whose linked report job belongs to another tenant.

        Admitting the batch is not enough: the item-to-job link was written by whichever
        worker dispatched it, so a link created before dispatch was tenant-scoped can point
        at another tenant's job. A same-tenant caller replaying such an item would otherwise
        read that job and derive a new one from it.

        The caller is told only that the item cannot be replayed - the existing 409 contract,
        which is true and discloses nothing about the other tenant.

        The item is quarantined only when it is in a state replay would otherwise have acted
        on. `mark_item_failed` has no source-status predicate: it rewrites whatever it is
        given, increments the attempt count, and can flip a completed batch to
        `completed_with_failures`. Applying it to a `succeeded` item would destroy finished
        work in response to a call that was never going to change anything, which is a worse
        outcome than the disclosure this refusal prevents.
        """

        report_job_id = item.report_job_id
        if report_job_id is None:
            return
        job = self._report_job_ledger.get_job(report_job_id)
        if job.tenant_id == batch.tenant_id:
            return

        # Observe unconditionally: the corrupt link is a fact regardless of what the caller
        # asked for, and a terminal item carrying one is the *stronger* signal - the dispatch
        # that wrote the link already happened, so a report exists against another tenant's
        # job. Recording only the states we also mutate would hide exactly those.
        self._logger.error(
            "batch_item_tenant_mismatch",
            extra={
                "extra_fields": {
                    "batch_id": batch.batch_id,
                    "batch_item_id": item.batch_item_id,
                    "report_job_id": report_job_id,
                    "item_status": item.status,
                    "failure_category": TENANT_MISMATCH_CATEGORY,
                    "command": "batch_item_replay",
                    "quarantined": branch is not None,
                }
            },
        )
        if branch is None:
            # Replay would not have acted on this item, so the refusal must not either.
            raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")
        self._batch_ledger.mark_item_failed(
            batch_item_id=item.batch_item_id,
            error_category=TENANT_MISMATCH_CATEGORY,
            error_summary=(
                "Linked report job belongs to a different tenant than its batch; "
                "replay refused and the item quarantined for operator review."
            ),
            retryable=False,
        )
        raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")

    def _source_job_for_item(self, item: ReportBatchItemRecord) -> ReportJobLedgerRecord:
        if self._replay_branch_for(item) != REPLAY_BRANCH_NEW or not item.report_job_id:
            raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")
        return self._report_job_ledger.get_job(item.report_job_id)

    def _source_job_for_replayed_job(
        self,
        *,
        batch: ReportBatchRecord,
        replayed_job: ReportJobLedgerRecord,
    ) -> ReportJobLedgerRecord:
        """Resolve the source job behind a replayed job, refusing a foreign-tenant source.

        This is one dereference further than the item-to-job link: the identifier comes from
        a lineage event payload, not from the batch item. A batch whose *replayed* job is
        same-tenant can still have a source job belonging to another tenant, so passing the
        fence on the replayed job says nothing about this one. Every identifier followed out
        of the batch is checked where it is followed, not where it was first held.
        """

        for event in self._report_job_ledger.list_status_events(replayed_job.job_id):
            source_job_id = event.event_payload.get("source_job_id")
            if event.event_type == "batch_item_replay_lineage_bound" and isinstance(
                source_job_id,
                str,
            ):
                source_job = self._report_job_ledger.get_job(source_job_id)
                if source_job.tenant_id != batch.tenant_id:
                    self._logger.error(
                        TENANT_MISMATCH_CATEGORY,
                        extra={
                            "extra_fields": {
                                "batch_id": batch.batch_id,
                                "report_job_id": replayed_job.job_id,
                                "source_job_id": source_job_id,
                                "failure_category": TENANT_MISMATCH_CATEGORY,
                                "command": "batch_item_replay_lineage",
                            }
                        },
                    )
                    raise InvalidReportJobTransitionError("report_batch_item_cannot_be_replayed")
                return source_job
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
