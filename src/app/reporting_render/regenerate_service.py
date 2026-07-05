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
    ReportJobRegenerateRequest,
    ReportStatusEvent,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.models import ReportInputSnapshotRecord
from app.reporting_lineage.service import (
    get_portfolio_review_snapshot_capture_service,
    get_report_input_snapshot_store,
)
from app.reporting_lineage.store import ReportInputSnapshotNotFoundError
from app.reporting_metrics import record_report_operation
from app.reporting_render.service import (
    get_portfolio_review_render_orchestration_service,
)


@dataclass(frozen=True)
class ReportRegenerateResult:
    source_job: ReportJobLedgerRecord
    regenerated_job: ReportJobLedgerRecord
    previous_snapshot: ReportInputSnapshotRecord | None
    new_snapshot: ReportInputSnapshotRecord | None
    idempotency_key: str


class RegenerateLedger(Protocol):
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


class RegenerateSnapshotStore(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> ReportInputSnapshotRecord: ...


class RegenerateCaptureService(Protocol):
    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


class RegenerateRenderService(Protocol):
    async def render_for_job(
        self,
        job: ReportJobLedgerRecord,
        *,
        supersedes_render_job_id: str | None = None,
        supersedes_archive_document_id: str | None = None,
        archive_consequence: str | None = None,
    ) -> ReportJobLedgerRecord: ...


class PortfolioReviewRegenerateService:
    def __init__(
        self,
        *,
        ledger: RegenerateLedger,
        snapshot_store: RegenerateSnapshotStore,
        capture_service: RegenerateCaptureService,
        render_service: RegenerateRenderService,
    ) -> None:
        self._ledger = ledger
        self._snapshot_store = snapshot_store
        self._capture_service = capture_service
        self._render_service = render_service

    async def regenerate_job(
        self,
        *,
        job_id: str,
        command: ReportJobRegenerateRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportRegenerateResult:
        source_job = self._ledger.get_job(job_id)
        _assert_regenerate_eligible(source_job)
        previous_snapshot = _optional_snapshot(self._snapshot_store, source_job.job_id)
        regenerate_key = _regenerate_idempotency_key(
            source_job_id=source_job.job_id,
            idempotency_key=idempotency_key,
        )
        request = PortfolioReviewJobRequest(
            portfolio_scope=source_job.portfolio_scope,
            as_of_date=source_job.as_of_date,
            requested_output_formats=source_job.requested_output_formats,
            reporting_currency=source_job.reporting_currency,
            options=source_job.options,
        )
        regenerated = self._ledger.create_portfolio_review_job(
            request=request,
            caller_context=caller_context,
            idempotency_key=regenerate_key,
        )
        if regenerated.status == "accepted":
            self._ledger.append_job_event(
                job_id=source_job.job_id,
                event_type="job_regenerate_requested",
                message=(
                    "Report regeneration requested from upstream data as "
                    f"{regenerated.job_id}: {command.reason}"
                ),
                event_payload={"regenerated_job_id": regenerated.job_id},
                event_idempotency_key=regenerate_key,
                actor=caller_context.triggered_by,
                correlation_id=caller_context.correlation_id,
                trace_id=caller_context.trace_id,
            )
            regenerated = await self._capture_service.capture_for_job(regenerated)
        if regenerated.status == "data_ready":
            regenerated = await self._render_service.render_for_job(
                regenerated,
                supersedes_render_job_id=source_job.render_job_id,
                supersedes_archive_document_id=source_job.archive_document_id,
                archive_consequence="replacement",
            )
        if regenerated.status == "archived":
            if not regenerated.archive_document_id:
                raise InvalidReportJobTransitionError(
                    "report_job_regenerate_archive_document_missing"
                )
            _append_source_event_once(
                self._ledger,
                job_id=source_job.job_id,
                event_type="job_regenerate_archived",
                regenerated_job_id=regenerated.job_id,
                message=(
                    "Report regeneration archived replacement document "
                    f"{regenerated.archive_document_id} from job {regenerated.job_id}."
                ),
                archive_document_id=regenerated.archive_document_id,
                caller_context=caller_context,
            )
        new_snapshot = _optional_snapshot(self._snapshot_store, regenerated.job_id)
        record_report_operation(
            operation="regenerate_from_upstream",
            status=regenerated.status,
            failure_category=regenerated.failure_category,
        )
        return ReportRegenerateResult(
            source_job=source_job,
            regenerated_job=regenerated,
            previous_snapshot=previous_snapshot,
            new_snapshot=new_snapshot,
            idempotency_key=idempotency_key or "",
        )


def get_portfolio_review_regenerate_service() -> PortfolioReviewRegenerateService:
    return PortfolioReviewRegenerateService(
        ledger=get_report_job_ledger(),
        snapshot_store=get_report_input_snapshot_store(),
        capture_service=get_portfolio_review_snapshot_capture_service(),
        render_service=get_portfolio_review_render_orchestration_service(),
    )


def _assert_regenerate_eligible(job: ReportJobLedgerRecord) -> None:
    if (
        job.status != "archived"
        or "pdf" not in job.requested_output_formats
        or not job.render_job_id
        or not job.archive_document_id
    ):
        raise InvalidReportJobTransitionError("report_job_cannot_be_regenerated")


def _regenerate_idempotency_key(*, source_job_id: str, idempotency_key: str | None) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise MissingIdempotencyKeyError("missing_idempotency_key")
    return f"regenerate:{source_job_id}:{idempotency_key.strip()}"


def _optional_snapshot(
    snapshot_store: RegenerateSnapshotStore,
    job_id: str,
) -> ReportInputSnapshotRecord | None:
    try:
        return snapshot_store.get_snapshot_by_job(job_id)
    except ReportInputSnapshotNotFoundError:
        return None


def _append_source_event_once(
    ledger: RegenerateLedger,
    *,
    job_id: str,
    event_type: str,
    regenerated_job_id: str,
    message: str,
    archive_document_id: str,
    caller_context: ReportCallerContext,
) -> None:
    if any(
        event.event_type == event_type
        and event.event_payload.get("regenerated_job_id") == regenerated_job_id
        for event in ledger.list_status_events(job_id)
    ):
        return
    ledger.append_job_event(
        job_id=job_id,
        event_type=event_type,
        message=message,
        event_payload={
            "regenerated_job_id": regenerated_job_id,
            "archive_document_id": archive_document_id,
        },
        event_idempotency_key=f"{event_type}:{job_id}:{regenerated_job_id}",
        actor=caller_context.triggered_by,
        correlation_id=caller_context.correlation_id,
        trace_id=caller_context.trace_id,
    )
