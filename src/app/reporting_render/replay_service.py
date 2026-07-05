from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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
    ReportJobReplayRequest,
    ReportStatusEvent,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_metrics import record_report_operation
from app.reporting_render.service import get_portfolio_review_render_orchestration_service


@dataclass(frozen=True)
class ReportReplayResult:
    source_job: ReportJobLedgerRecord
    replayed_job: ReportJobLedgerRecord
    idempotency_key: str


class ReplayLedger(Protocol):
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


class ReplayCaptureService(Protocol):
    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


class ReplayRenderService(Protocol):
    async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


class PortfolioReviewReplayService:
    def __init__(
        self,
        *,
        ledger: ReplayLedger,
        capture_service: ReplayCaptureService,
        render_service: ReplayRenderService,
    ) -> None:
        self._ledger = ledger
        self._capture_service = capture_service
        self._render_service = render_service

    async def replay_job(
        self,
        *,
        job_id: str,
        command: ReportJobReplayRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportReplayResult:
        source_job = self._ledger.get_job(job_id)
        assert_replay_eligible(source_job)
        replay_key = replay_idempotency_key(
            source_job_id=source_job.job_id,
            idempotency_key=idempotency_key,
        )
        replayed = self._ledger.create_portfolio_review_job(
            request=portfolio_review_request_from_job(source_job),
            caller_context=caller_context,
            idempotency_key=replay_key,
        )
        _upsert_replay_relationship(
            self._ledger,
            source_job=source_job,
            replayed=replayed,
            reason=command.reason,
            actor=caller_context.triggered_by,
        )
        if replayed.status == "accepted":
            self._ledger.append_job_event(
                job_id=source_job.job_id,
                event_type="job_replay_requested",
                message=f"Report replay requested as {replayed.job_id}: {command.reason}",
                event_payload={"replayed_job_id": replayed.job_id},
                event_idempotency_key=replay_key,
                actor=caller_context.triggered_by,
                correlation_id=caller_context.correlation_id,
                trace_id=caller_context.trace_id,
            )
            replayed = await self._capture_service.capture_for_job(replayed)
        if replayed.status == "data_ready" and "pdf" in replayed.requested_output_formats:
            replayed = await self._render_service.render_for_job(replayed)
        if replayed.status in {
            "data_ready",
            "completed",
            "completed_with_warnings",
            "archived",
            "failed",
        }:
            append_source_event_once(
                self._ledger,
                job_id=source_job.job_id,
                event_type="job_replay_completed",
                replayed_job_id=replayed.job_id,
                replayed_status=replayed.status,
                message=(
                    f"Report replay completed as {replayed.job_id} with status {replayed.status}."
                ),
                caller_context=caller_context,
            )
        _upsert_replay_relationship(
            self._ledger,
            source_job=source_job,
            replayed=replayed,
            reason=command.reason,
            actor=caller_context.triggered_by,
        )
        record_report_operation(
            operation="replay_command",
            status=replayed.status,
            failure_category=replayed.failure_category,
        )
        return ReportReplayResult(
            source_job=source_job,
            replayed_job=replayed,
            idempotency_key=idempotency_key or "",
        )


def get_portfolio_review_replay_service() -> PortfolioReviewReplayService:
    return PortfolioReviewReplayService(
        ledger=get_report_job_ledger(),
        capture_service=get_portfolio_review_snapshot_capture_service(),
        render_service=get_portfolio_review_render_orchestration_service(),
    )


def assert_replay_eligible(job: ReportJobLedgerRecord) -> None:
    if job.status != "failed" or not job.retry_eligible:
        raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")
    if job.archive_document_id:
        raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")


def replay_idempotency_key(*, source_job_id: str, idempotency_key: str | None) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise MissingIdempotencyKeyError("missing_idempotency_key")
    return f"replay:{source_job_id}:{idempotency_key.strip()}"


def portfolio_review_request_from_job(job: ReportJobLedgerRecord) -> PortfolioReviewJobRequest:
    return PortfolioReviewJobRequest(
        portfolio_scope=job.portfolio_scope,
        as_of_date=job.as_of_date,
        requested_output_formats=job.requested_output_formats,
        reporting_currency=job.reporting_currency,
        options=job.options,
    )


def append_source_event_once(
    ledger: ReplayLedger,
    *,
    job_id: str,
    event_type: str,
    replayed_job_id: str,
    replayed_status: str,
    message: str,
    caller_context: ReportCallerContext,
    event_payload: dict[str, object] | None = None,
) -> None:
    if any(
        event.event_type == event_type
        and event.event_payload.get("replayed_job_id") == replayed_job_id
        for event in ledger.list_status_events(job_id)
    ):
        return
    payload = {
        "replayed_job_id": replayed_job_id,
        "replayed_status": replayed_status,
        **(event_payload or {}),
    }
    ledger.append_job_event(
        job_id=job_id,
        event_type=event_type,
        message=message,
        event_payload=payload,
        event_idempotency_key=f"{event_type}:{job_id}:{replayed_job_id}",
        actor=caller_context.triggered_by,
        correlation_id=caller_context.correlation_id,
        trace_id=caller_context.trace_id,
    )


def _upsert_replay_relationship(
    ledger: ReplayLedger,
    *,
    source_job: ReportJobLedgerRecord,
    replayed: ReportJobLedgerRecord,
    reason: str,
    actor: str,
) -> None:
    ledger.upsert_job_relationship(
        source_job=source_job,
        derived_job=replayed,
        relationship_type="failed_work_replay",
        actor=actor,
        reason=reason,
        new_archive_document_id=replayed.archive_document_id,
    )
