"""Read-only notice capture using durable Report records and authorized Archive reads."""

import json
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol

from fastapi.responses import JSONResponse

from app.composite_reporting.correction_notice.admission import build_notice, require_equal
from app.composite_reporting.correction_notice.errors import NoticeEvidenceRefused
from app.composite_reporting.correction_notice.evidence import acknowledged_path
from app.composite_reporting.correction_notice.selection import CorrectionNoticeSelection
from app.composite_reporting.correction_notice.source_capture import generation_from_capture
from app.composite_reporting.eligibility_tables import CAPACITY_POLICY
from app.reporting_identity.snapshot_lifecycle import read_reproduction_availability
from app.reporting_jobs.models import ReportCallerContext, ReportJobLedgerRecord, ReportStatusEvent
from app.reporting_jobs.visibility import assert_job_visible
from app.reporting_lineage.capture_service import (
    PortfolioReviewInputCapture,
    PortfolioReviewInputCaptureError,
    _RecordedUpstreamCall,
)
from app.reporting_lineage.models import ReportInputSnapshotRecord


@dataclass(frozen=True)
class ArchiveNoticeRead:
    status_code: int
    payload: dict[str, Any]
    response_bytes: bytes
    endpoint: str


class NoticeArchiveReader(Protocol):
    """Archive197 owns the concrete native path, DTO and privileged authorization."""

    async def get_notice_document(
        self, document_id: str, *, caller: ReportCallerContext
    ) -> ArchiveNoticeRead: ...

    async def get_notice_relationship(
        self, source_document_id: str, relationship_id: str, *, caller: ReportCallerContext
    ) -> ArchiveNoticeRead: ...


class NoticeSourceLedger(Protocol):
    def get_job(self, job_id: str) -> ReportJobLedgerRecord: ...
    def list_status_events(self, job_id: str) -> list[ReportStatusEvent]: ...


class NoticeSnapshotStore(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> ReportInputSnapshotRecord: ...


def native_snapshot_read_bytes(record: ReportInputSnapshotRecord) -> bytes:
    """The current native snapshot read body, not recovered historical HTTP bytes.

    The local immutable store returns the same response model as the registered
    snapshot read API. Apply its native JSON serialization after authorization.
    Historical fixture hashes remain separate and are never used as runtime data.
    """
    return bytes(JSONResponse(record.model_dump(mode="json")).body)


class CorrectionNoticeInputProvider:
    def __init__(
        self,
        *,
        ledger: NoticeSourceLedger,
        snapshots: NoticeSnapshotStore,
        archive_reader: NoticeArchiveReader | None,
    ):
        self._ledger, self._snapshots, self._archive = ledger, snapshots, archive_reader

    async def collect_for_job(self, job: ReportJobLedgerRecord) -> PortfolioReviewInputCapture:
        calls: list[_RecordedUpstreamCall] = []
        try:
            if self._archive is None:
                raise NoticeEvidenceRefused("COMPOSITE_NOTICE_ARCHIVE_READER_NOT_CONFIGURED")
            selection = CorrectionNoticeSelection.model_validate(
                job.options.get("composite_notice_selection")
            )
            caller = ReportCallerContext(
                triggered_by=job.triggered_by,
                caller_application=job.caller_application,
                tenant_id=job.tenant_id,
                region=job.region,
                booking_center_code=job.booking_center_code,
                role=job.role,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
            require_equal(
                (
                    selection.tenant_id,
                    selection.composite_id,
                    selection.period_end,
                    selection.reporting_currency,
                ),
                (
                    job.tenant_id,
                    job.portfolio_scope.get("composite_id"),
                    job.as_of_date,
                    job.reporting_currency,
                ),
                "ORDER_SCOPE",
            )
            source_jobs = {}
            # Fence both persisted jobs, including booking centre, before any
            # source read/comparison or Archive disclosure.
            for side in ("before", "after"):
                source_jobs[side] = self._ledger.get_job(getattr(selection, side).report_job_id)
                assert_job_visible(source_jobs[side], caller)
            total_bytes = 0
            generations, events = {}, {}

            def budget(raw: bytes) -> None:
                nonlocal total_bytes
                total_bytes += len(raw)
                if total_bytes > CAPACITY_POLICY["max_request_body_bytes"]:
                    raise NoticeEvidenceRefused("COMPOSITE_NOTICE_CAPTURE_CAPACITY_EXCEEDED")

            for side, source_job in source_jobs.items():
                snapshot = self._snapshots.get_snapshot_by_job(source_job.job_id)
                availability = read_reproduction_availability(
                    (snapshot.lifecycle or {}).get("reproduction_availability")
                )
                if availability != "snapshot_recomposition":
                    raise NoticeEvidenceRefused("COMPOSITE_NOTICE_SOURCE_UNAVAILABLE")
                raw = native_snapshot_read_bytes(snapshot)
                budget(raw)
                generations[side] = generation_from_capture(
                    selection=getattr(selection, side),
                    source_job=source_job,
                    snapshot=snapshot,
                    response_bytes=raw,
                    tenant_id=job.tenant_id,
                    region=job.region,
                )
                require_equal(
                    selection.period_start.isoformat(),
                    generations[side]["source_identity"]["period_start"],
                    "ORDER_SCOPE",
                )
                events[side] = [
                    event.model_dump(mode="json")
                    for event in self._ledger.list_status_events(source_job.job_id)
                ]
                budget(
                    bytes(
                        JSONResponse(
                            {"report_job_id": source_job.job_id, "events": events[side]}
                        ).body
                    )
                )

            async def read(awaitable: Awaitable[ArchiveNoticeRead]) -> ArchiveNoticeRead:
                started = perf_counter()
                result = await awaitable
                budget(result.response_bytes)
                if result.status_code != 200:
                    raise NoticeEvidenceRefused("COMPOSITE_NOTICE_ARCHIVE_EVIDENCE_UNAVAILABLE")
                require_equal(json.loads(result.response_bytes), result.payload, "READ_BODY")
                calls.append(
                    _RecordedUpstreamCall(
                        service_name="lotus-archive",
                        endpoint=result.endpoint,
                        method="GET",
                        contract_version="v1",
                        request_payload={},
                        response_payload=result.payload,
                        response_ref=None,
                        status_code=result.status_code,
                        latency_ms=max(0, int((perf_counter() - started) * 1000)),
                        supportability_status="partial",
                        completeness_status="partial",
                        failure_category="partial_data",
                        failure_message="Notice is not attested.",
                        captured_at=datetime.now(UTC),
                        correlation_id=job.correlation_id,
                        trace_id=job.trace_id,
                    )
                )
                return result

            documents = []
            for document_id in selection.source_document_ids:
                result = await read(self._archive.get_notice_document(document_id, caller=caller))
                documents.append(result.payload)
            relationship = await read(
                self._archive.get_notice_relationship(
                    selection.source_document_ids[1], selection.relationship_ids[1], caller=caller
                )
            )
            path = acknowledged_path(
                selection=selection,
                generations=generations,
                documents=documents,
                relationship_record=relationship.payload,
                report_events=events,
                relationship_source_capture=relationship.endpoint,
            )
            payload = build_notice(
                tenant_id=job.tenant_id, generations=generations, archive_path=path
            )
            return PortfolioReviewInputCapture(snapshot_payload=payload, upstream_calls=calls)
        except NoticeEvidenceRefused as exc:
            raise PortfolioReviewInputCaptureError(
                original_error=exc, upstream_calls=calls
            ) from None
        except Exception as exc:
            # Supplier validation and segregation errors must not expose IDs,
            # operator reason/actor text, raw Pydantic errors or existence.
            raise PortfolioReviewInputCaptureError(
                original_error=NoticeEvidenceRefused("COMPOSITE_NOTICE_SOURCE_EVIDENCE_REFUSED"),
                upstream_calls=calls,
            ) from exc
