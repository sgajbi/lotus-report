"""Controlled native capture ports; no restored database or Archive197 API claim."""

from copy import deepcopy

import pytest
from fastapi.responses import JSONResponse

from app.composite_reporting.correction_notice.capture import (
    ArchiveNoticeRead,
    CorrectionNoticeInputProvider,
    native_snapshot_read_bytes,
)
from app.composite_reporting.eligibility_tables import CAPACITY_POLICY
from app.reporting_jobs.models import ReportStatusEvent
from app.reporting_lineage.capture_service import PortfolioReviewInputCaptureError
from tests.unit.composite_notice_fixture import (
    notice_fixture,
    notice_job_and_capture,
    retained,
    retained_source_job_and_capture,
)


class ControlledNoticeSources:
    def __init__(self, notice):
        self.notice = notice
        self.jobs, self.snapshots, self.events = {}, {}, {}
        self.calls = []
        for side, name in (
            ("before", "consumer-0105-source-off-events-original-response.bin"),
            ("after", "consumer-0111-source-off-events-corrected-response.bin"),
        ):
            job, snapshot, _ = retained_source_job_and_capture(side, notice)
            self.jobs[job.job_id], self.snapshots[job.job_id] = job, snapshot
            self.events[job.job_id] = [
                ReportStatusEvent.model_validate(event) for event in retained(name)["events"]
            ]

    def get_job(self, job_id):
        return self.jobs[job_id]

    def get_snapshot_by_job(self, job_id):
        return self.snapshots[job_id]

    def list_status_events(self, job_id):
        return self.events[job_id]

    async def get_notice_document(self, document_id, *, caller):
        self.calls.append(("document", document_id, caller.tenant_id))
        raw = deepcopy(
            next(
                node["document_response"]
                for node in self.notice["archive_path"]["nodes"]
                if node["document_id"] == document_id
            )
        )
        return ArchiveNoticeRead(
            200, raw, JSONResponse(raw).body, "CONTROLLED_DOCUMENT_READ:" + document_id
        )

    async def get_notice_relationship(self, source_document_id, relationship_id, *, caller):
        self.calls.append(("relationship", source_document_id, relationship_id, caller.tenant_id))
        raw = deepcopy(self.notice["archive_path"]["edges"][1]["acknowledgement"]["raw_payload"])
        return ArchiveNoticeRead(
            200,
            raw,
            JSONResponse(raw).body,
            "CONTROLLED_RELATIONSHIP_RECORD_NOT_ARCHIVE197_API_READ",
        )


@pytest.mark.asyncio
async def test_native_capture_composes_owned_records_without_financial_source_calls():
    fixture = notice_fixture()
    job, _ = notice_job_and_capture(fixture)
    sources = ControlledNoticeSources(fixture)
    result = await CorrectionNoticeInputProvider(
        ledger=sources, snapshots=sources, archive_reader=sources
    ).collect_for_job(job)
    assert len(sources.calls) == 5
    assert len(result.upstream_calls) == 5
    assert len(result.snapshot_payload["tables"]) == 32
    for side in ("before", "after"):
        assert (
            result.snapshot_payload["generations"][side]["payload"]
            == fixture["generations"][side]["payload"]
        )
    ack = result.snapshot_payload["archive_path"]["edges"][1]["acknowledgement"]
    assert ack["kind"] == "ARCHIVE_RELATIONSHIP_RECORD"
    assert "current_document_id" not in ack["raw_payload"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["tenant", "region", "booking_center", "snapshot_unavailable"])
async def test_scope_and_retention_refuse_before_archive_read(fault):
    fixture = notice_fixture()
    job, _ = notice_job_and_capture(fixture)
    sources = ControlledNoticeSources(fixture)
    source_id = fixture["generations"]["before"]["selection"]["report_job_id"]
    if fault == "snapshot_unavailable":
        sources.snapshots[source_id] = sources.snapshots[source_id].model_copy(
            update={"lifecycle": {"reproduction_availability": "none"}}
        )
    else:
        field = {
            "tenant": "tenant_id",
            "region": "region",
            "booking_center": "booking_center_code",
        }[fault]
        sources.jobs[source_id] = sources.jobs[source_id].model_copy(update={field: "foreign"})
    with pytest.raises(PortfolioReviewInputCaptureError) as error:
        await CorrectionNoticeInputProvider(
            ledger=sources, snapshots=sources, archive_reader=sources
        ).collect_for_job(job)
    assert str(error.value.original_error).startswith("COMPOSITE_NOTICE_")
    assert "foreign" not in str(error.value)
    assert sources.calls == []


@pytest.mark.asyncio
async def test_missing_privileged_reader_refuses_without_source_reads():
    fixture = notice_fixture()
    job, _ = notice_job_and_capture(fixture)
    with pytest.raises(PortfolioReviewInputCaptureError, match="ARCHIVE_READER_NOT_CONFIGURED"):
        await CorrectionNoticeInputProvider(
            ledger=None, snapshots=None, archive_reader=None
        ).collect_for_job(job)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["duplicate", "pending"])
async def test_technical_edge_requires_one_acknowledged_lineage_event(fault):
    fixture = notice_fixture()
    job, _ = notice_job_and_capture(fixture)
    sources = ControlledNoticeSources(fixture)
    source_id = fixture["generations"]["before"]["selection"]["report_job_id"]
    acknowledged = next(
        event
        for event in sources.events[source_id]
        if event.event_type == "job_archive_lineage_recorded"
    )
    if fault == "duplicate":
        sources.events[source_id].append(acknowledged)
    else:
        sources.events[source_id] = [
            event.model_copy(update={"event_type": "job_archive_lineage_pending"})
            if event is acknowledged
            else event
            for event in sources.events[source_id]
        ]
    with pytest.raises(PortfolioReviewInputCaptureError) as error:
        await CorrectionNoticeInputProvider(
            ledger=sources, snapshots=sources, archive_reader=sources
        ).collect_for_job(job)
    assert str(error.value.original_error) == "COMPOSITE_NOTICE_SOURCE_EVIDENCE_REFUSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("overflow", [False, True])
async def test_aggregate_capture_byte_boundary_includes_all_native_reads(overflow):
    fixture = notice_fixture()
    job, _ = notice_job_and_capture(fixture)
    sources = ControlledNoticeSources(fixture)
    total = sum(len(native_snapshot_read_bytes(record)) for record in sources.snapshots.values())
    total += sum(
        len(
            JSONResponse(
                {
                    "report_job_id": key,
                    "events": [event.model_dump(mode="json") for event in events],
                }
            ).body
        )
        for key, events in sources.events.items()
    )
    total += sum(
        len(JSONResponse(node["document_response"]).body)
        for node in fixture["archive_path"]["nodes"]
    )
    relationship = await sources.get_notice_relationship("unused", "unused", caller=job)
    total += len(relationship.response_bytes)
    sources.calls.clear()
    padding = CAPACITY_POLICY["max_request_body_bytes"] - total + int(overflow)
    assert padding > 0

    async def padded_relationship(source_document_id, relationship_id, *, caller):
        result = await ControlledNoticeSources.get_notice_relationship(
            sources, source_document_id, relationship_id, caller=caller
        )
        return ArchiveNoticeRead(
            result.status_code,
            result.payload,
            result.response_bytes + b" " * padding,
            result.endpoint,
        )

    sources.get_notice_relationship = padded_relationship
    provider = CorrectionNoticeInputProvider(
        ledger=sources, snapshots=sources, archive_reader=sources
    )
    if overflow:
        with pytest.raises(PortfolioReviewInputCaptureError) as error:
            await provider.collect_for_job(job)
        assert str(error.value.original_error) == "COMPOSITE_NOTICE_CAPTURE_CAPACITY_EXCEEDED"
    else:
        assert len((await provider.collect_for_job(job)).snapshot_payload["tables"]) == 32
