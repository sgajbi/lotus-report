from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobNotFoundError,
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
from app.reporting_lineage.service import (
    get_portfolio_review_snapshot_capture_service,
    get_report_input_snapshot_store,
)
from app.reporting_lineage.store import (
    ReportInputSnapshotCreateRequest,
    ReportInputSnapshotNotFoundError,
    ReportInputSnapshotRecord,
)
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

    def mark_collecting_data(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord: ...

    def mark_data_ready(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord: ...

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


class ReplayEventLedger(Protocol):
    def list_status_events(self, job_id: str) -> list[ReportStatusEvent]: ...

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


class ReplayCaptureService(Protocol):
    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


class ReplaySnapshotStore(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> ReportInputSnapshotRecord: ...

    def create_snapshot(
        self, request: ReportInputSnapshotCreateRequest
    ) -> ReportInputSnapshotRecord: ...


class ReplayRenderService(Protocol):
    async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


class PortfolioReviewReplayService:
    def __init__(
        self,
        *,
        ledger: ReplayLedger,
        capture_service: ReplayCaptureService,
        render_service: ReplayRenderService,
        snapshot_store: ReplaySnapshotStore | None = None,
    ) -> None:
        self._ledger = ledger
        self._capture_service = capture_service
        self._render_service = render_service
        self._snapshot_store = snapshot_store

    async def replay_job(
        self,
        *,
        job_id: str,
        command: ReportJobReplayRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportReplayResult:
        source_job = self._ledger.get_job(job_id)
        # Tenant and region are segregation boundaries: a caller must never be
        # able to materialize another tenant's report evidence into a document
        # under its own context. Mismatches answer exactly like an unknown id.
        if (
            source_job.tenant_id != caller_context.tenant_id
            or source_job.region != caller_context.region
        ):
            raise ReportJobNotFoundError("report_job_not_found")
        assert_replay_eligible(source_job)
        source_snapshot = self._require_retained_snapshot(source_job)
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
            replayed = await self._collect_replay_inputs(
                source_snapshot=source_snapshot,
                source_job=source_job,
                replayed=replayed,
                caller_context=caller_context,
            )
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

    def _require_retained_snapshot(
        self, source_job: ReportJobLedgerRecord
    ) -> ReportInputSnapshotRecord | None:
        # The render_artifact_unrecoverable posture promises recovery from the
        # retained snapshot. If that snapshot no longer exists, recollecting
        # current upstream state would silently produce a document with
        # different evidence under a failed-work-replay relationship, so the
        # replay is refused instead - before any replayed job is created.
        if (
            source_job.failure_category != "render_artifact_unrecoverable"
            or self._snapshot_store is None
        ):
            return None
        try:
            return self._snapshot_store.get_snapshot_by_job(source_job.job_id)
        except ReportInputSnapshotNotFoundError as exc:
            raise InvalidReportJobTransitionError("report_job_cannot_be_replayed") from exc

    async def _collect_replay_inputs(
        self,
        *,
        source_snapshot: ReportInputSnapshotRecord | None,
        source_job: ReportJobLedgerRecord,
        replayed: ReportJobLedgerRecord,
        caller_context: ReportCallerContext,
    ) -> ReportJobLedgerRecord:
        if source_snapshot is not None:
            return self._clone_retained_snapshot(
                source_snapshot=source_snapshot,
                source_job=source_job,
                replayed=replayed,
                caller_context=caller_context,
            )
        return await self._capture_service.capture_for_job(replayed)

    def _clone_retained_snapshot(
        self,
        *,
        source_snapshot: ReportInputSnapshotRecord,
        source_job: ReportJobLedgerRecord,
        replayed: ReportJobLedgerRecord,
        caller_context: ReportCallerContext,
    ) -> ReportJobLedgerRecord:
        # An artifactless-replay failure happened after a successful capture,
        # so the source snapshot is the validated as-of truth for this report
        # and reusing it keeps the recovery deterministic even when upstream
        # state has moved on.
        assert self._snapshot_store is not None
        job = self._ledger.mark_collecting_data(
            job_id=replayed.job_id,
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
        )
        try:
            cloned_snapshot = self._snapshot_store.get_snapshot_by_job(job.job_id)
        except ReportInputSnapshotNotFoundError:
            cloned_snapshot = self._snapshot_store.create_snapshot(
                ReportInputSnapshotCreateRequest(
                    report_job_id=job.job_id,
                    report_type=source_snapshot.report_type,
                    report_data_contract_version=source_snapshot.report_data_contract_version,
                    portfolio_scope=source_snapshot.portfolio_scope,
                    as_of_date=source_snapshot.as_of_date,
                    snapshot_payload=source_snapshot.snapshot_payload,
                    snapshot_storage_ref=source_snapshot.snapshot_storage_ref,
                    supportability_status=source_snapshot.supportability_status,
                    completeness_status=source_snapshot.completeness_status,
                    lineage_summary=_cloned_lineage_summary(
                        source_snapshot=source_snapshot,
                        source_job_id=source_job.job_id,
                    ),
                    captured_at=datetime.now(UTC),
                    correlation_id=caller_context.correlation_id,
                    trace_id=caller_context.trace_id,
                )
            )
        self._ledger.append_job_event(
            job_id=job.job_id,
            event_type="job_replay_snapshot_cloned",
            message=(
                f"Replay reused retained input snapshot {source_snapshot.snapshot_id} "
                f"from {source_job.job_id} without recollecting upstream data."
            ),
            event_payload={
                "source_snapshot_id": source_snapshot.snapshot_id,
                "cloned_snapshot_id": cloned_snapshot.snapshot_id,
                "replayed_job_id": job.job_id,
            },
            event_idempotency_key=f"job_replay_snapshot_cloned:{job.job_id}",
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
        )
        return self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
        )


def _cloned_lineage_summary(
    *,
    source_snapshot: ReportInputSnapshotRecord,
    source_job_id: str,
) -> dict[str, object]:
    """Lineage for a cloned snapshot: no upstream calls were made for this
    job, so every per-snapshot call counter must be zero (the lineage read
    joins calls by snapshot id and would otherwise contradict the summary).
    The data's service provenance and captured posture stay, and the source
    snapshot id names where the original call evidence lives."""

    summary = dict(source_snapshot.lineage_summary)
    summary.update(
        {
            "call_count": 0,
            "partial_call_count": 0,
            "unavailable_call_count": 0,
            "not_supported_call_count": 0,
            "redacted_call_count": 0,
            "upstream_evidence": "cloned_from_source_snapshot",
            "source_call_count": source_snapshot.lineage_summary.get("call_count", 0),
            "cloned_from_report_job_id": source_job_id,
            "cloned_from_snapshot_id": source_snapshot.snapshot_id,
        }
    )
    return summary


def get_portfolio_review_replay_service() -> PortfolioReviewReplayService:
    return PortfolioReviewReplayService(
        ledger=get_report_job_ledger(),
        capture_service=get_portfolio_review_snapshot_capture_service(),
        render_service=get_portfolio_review_render_orchestration_service(),
        snapshot_store=get_report_input_snapshot_store(),
    )


def assert_replay_eligible(job: ReportJobLedgerRecord) -> None:
    # The replay command recreates a portfolio-review order; replaying any
    # other report type here would silently morph it into a portfolio review.
    # Other report families recover by resubmitting their own order, which is
    # equally deterministic because their render packages build from the
    # retained job request.
    if job.report_type != "portfolio_review":
        raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")
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
    ledger: ReplayEventLedger,
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
