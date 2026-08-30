from datetime import UTC, datetime

import pytest

from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobReplayRequest,
)
from app.reporting_lineage.store import (
    ReportInputSnapshotCreateRequest,
    ReportInputSnapshotStore,
)
from app.reporting_render.replay_service import (
    PortfolioReviewReplayService,
    assert_replay_eligible,
    get_portfolio_review_replay_service,
    portfolio_review_request_from_job,
    replay_idempotency_key,
)


def _caller() -> ReportCallerContext:
    return ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id="corr-replay",
        trace_id="trace-replay",
    )


def _request(*, output_formats: list[str] | None = None) -> PortfolioReviewJobRequest:
    return PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        as_of_date="2026-04-22",
        requested_output_formats=output_formats or ["json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


class _CaptureToDataReady:
    def __init__(self, ledger: ReportJobLedger) -> None:
        self._ledger = ledger
        self.calls = 0

    async def capture_for_job(self, job):
        self.calls += 1
        return self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )


class _RenderNotCalled:
    async def render_for_job(self, job):
        raise AssertionError("JSON replay must not invoke PDF render")


class _RenderToFailed:
    def __init__(self, ledger: ReportJobLedger) -> None:
        self._ledger = ledger
        self.calls = 0

    async def render_for_job(self, job):
        self.calls += 1
        return self._ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category="render_execution_failed",
            failure_message="lotus-render timed out.",
            retry_eligible=True,
        )


def _failed_job(
    ledger: ReportJobLedger,
    *,
    retry_eligible: bool = True,
    output_formats: list[str] | None = None,
):
    job = ledger.create_portfolio_review_job(
        request=_request(output_formats=output_formats),
        caller_context=_caller(),
        idempotency_key="source-replay",
    )
    return ledger.mark_failed(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
        failure_category="upstream_data_failed",
        failure_message="Upstream timeout.",
        retry_eligible=retry_eligible,
    )


@pytest.mark.asyncio
async def test_report_replay_json_path_completes_without_render(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _failed_job(ledger)
    capture = _CaptureToDataReady(ledger)
    service = PortfolioReviewReplayService(
        ledger=ledger,
        capture_service=capture,
        render_service=_RenderNotCalled(),
    )

    result = await service.replay_job(
        job_id=source.job_id,
        command=ReportJobReplayRequest(reason="Retry after upstream recovered."),
        caller_context=_caller(),
        idempotency_key="json-replay",
    )

    assert capture.calls == 1
    assert result.source_job.job_id == source.job_id
    assert result.replayed_job.status == "data_ready"
    assert result.replayed_job.requested_output_formats == ["json"]
    assert [
        event.event_type
        for event in ledger.list_status_events(source.job_id)
        if event.event_type == "job_replay_completed"
    ] == ["job_replay_completed"]


@pytest.mark.asyncio
async def test_report_replay_records_failed_render_result(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _failed_job(ledger, output_formats=["pdf"])
    capture = _CaptureToDataReady(ledger)
    render = _RenderToFailed(ledger)
    service = PortfolioReviewReplayService(
        ledger=ledger,
        capture_service=capture,
        render_service=render,
    )

    result = await service.replay_job(
        job_id=source.job_id,
        command=ReportJobReplayRequest(reason="Retry failed PDF render path."),
        caller_context=_caller(),
        idempotency_key="pdf-render-failure",
    )

    assert capture.calls == 1
    assert render.calls == 1
    assert result.replayed_job.status == "failed"
    assert result.replayed_job.failure_category == "render_execution_failed"
    relationships = ledger.list_job_relationships(source.job_id)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "failed_work_replay"
    assert relationships[0].derived_report_job_id == result.replayed_job.job_id
    assert relationships[0].derived_status == "failed"
    assert relationships[0].derived_failure_category == "render_execution_failed"
    assert [
        event.event_type
        for event in ledger.list_status_events(source.job_id)
        if event.event_type == "job_replay_completed"
    ] == ["job_replay_completed"]


def test_report_replay_rejects_missing_key_nonretryable_and_archived(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    failed = _failed_job(ledger, retry_eligible=False)
    # Build from a retry-eligible copy: the archive guard must be what fires,
    # not the retry check (the ledger helper reuses one idempotency key, so a
    # second create would silently return the first record).
    archived_with_doc = failed.model_copy(
        update={"retry_eligible": True, "archive_document_id": "doc_existing"}
    )

    with pytest.raises(MissingIdempotencyKeyError):
        replay_idempotency_key(source_job_id=failed.job_id, idempotency_key=" ")
    with pytest.raises(InvalidReportJobTransitionError):
        assert_replay_eligible(failed)
    with pytest.raises(InvalidReportJobTransitionError):
        assert_replay_eligible(archived_with_doc)


def test_report_replay_service_factory_wires_runtime_dependencies(monkeypatch):
    ledger = object()
    capture_service = object()
    render_service = object()
    snapshot_store = object()

    monkeypatch.setattr(
        "app.reporting_render.replay_service.get_report_job_ledger",
        lambda: ledger,
    )
    monkeypatch.setattr(
        "app.reporting_render.replay_service.get_portfolio_review_snapshot_capture_service",
        lambda: capture_service,
    )
    monkeypatch.setattr(
        "app.reporting_render.replay_service.get_portfolio_review_render_orchestration_service",
        lambda: render_service,
    )
    monkeypatch.setattr(
        "app.reporting_render.replay_service.get_report_input_snapshot_store",
        lambda: snapshot_store,
    )

    service = get_portfolio_review_replay_service()

    assert service._ledger is ledger
    assert service._capture_service is capture_service
    assert service._render_service is render_service
    assert service._snapshot_store is snapshot_store


_SNAPSHOT_PAYLOAD: dict = {
    "readiness": {"status": "ready"},
    "reportingCurrency": "USD",
    "reviewPeriod": {"label": "YTD"},
    "clientProfile": {
        "identity": {"client_name": "Alex Tan"},
        "mandate_profile": {"risk_exposure": "balanced"},
    },
    "overview": {"total_market_value": 100.0, "currency": "USD"},
}


def _create_snapshot_for(store: ReportInputSnapshotStore, job) -> None:
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=_SNAPSHOT_PAYLOAD,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-core"], "call_count": 1},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )


class _RefusingCapture:
    """Fails the test if the replay recollects data instead of cloning."""

    async def capture_for_job(self, job):
        raise AssertionError("replay must clone the retained snapshot, not recapture")


class _RecapturingCapture:
    """Snapshot-creating capture used to prove the fallback path."""

    def __init__(self, ledger: ReportJobLedger, store: ReportInputSnapshotStore) -> None:
        self._ledger = ledger
        self._store = store
        self.called = False

    async def capture_for_job(self, job):
        self.called = True
        _create_snapshot_for(self._store, job)
        return self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )


class _RecordingRenderClient:
    """Returns rendered-with-artifact and records every submitted render_job_id."""

    def __init__(
        self,
        *,
        fingerprint: str = "typst-0.14.2:aaaa1111",
        runtime_version: str = "0.14.2",
        artifact_base64: str | None = "JVBERi0xLjQKJQ==",
    ) -> None:
        self.render_job_ids: list[str] = []
        self._fingerprint = fingerprint
        self._runtime_version = runtime_version
        self._artifact_base64 = artifact_base64

    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        self.render_job_ids.append(payload["render_job_id"])
        response = {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": "portfolio-review",
            "template_version": "v1",
            "artifact_sha256": "sha256:recovered-artifact",
            "bounded_determinism_fingerprint": self._fingerprint,
            "runtime_engine": "typst",
            "runtime_engine_version": self._runtime_version,
        }
        if self._artifact_base64 is not None:
            response["artifact_base64"] = self._artifact_base64
        return 201, response


class _RecordingArchiveClient:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def archive_document(self, payload, **kwargs):
        self.payloads.append(payload)
        return 201, {"document_id": f"doc_recovered_{len(self.payloads)}"}


def _artifactless_failed_source(ledger: ReportJobLedger):
    source = ledger.create_portfolio_review_job(
        request=_request(output_formats=["pdf"]),
        caller_context=_caller(),
        idempotency_key="source-artifactless",
    )
    return ledger.mark_failed(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
        failure_category="render_artifact_unrecoverable",
        failure_message="Artifact only existed in the original render response.",
        retry_eligible=True,
    )


def _recovery_services(ledger, store, capture_service, *, snapshot_store, render_client=None):
    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    render_client = render_client or _RecordingRenderClient()
    archive_client = _RecordingArchiveClient()
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=render_client,
        archive_client=archive_client,
        snapshot_store=store,
        job_ledger=ledger,
    )
    replay_service = PortfolioReviewReplayService(
        ledger=ledger,
        capture_service=capture_service,
        render_service=render_service,
        snapshot_store=snapshot_store,
    )
    return replay_service, render_client, archive_client


@pytest.mark.asyncio
async def test_artifactless_render_failure_recovers_end_to_end_through_replay(tmp_path):
    """The timeout-after-successful-render proof: a job failed with
    render_artifact_unrecoverable is replay-eligible, the replay CLONES the
    retained snapshot (upstream data is never recollected), renders under a
    FRESH render job id - so it can never re-hit the artifactless terminal
    render job - and archives exactly one document without duplicating the
    original (which never reached archive)."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    _create_snapshot_for(store, failed_source)
    assert_replay_eligible(failed_source)

    replay_service, render_client, archive_client = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store
    )

    result = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Recover artifactless render."),
        caller_context=_caller(),
        idempotency_key="recover-artifactless",
    )

    replayed = result.replayed_job
    assert replayed.job_id != failed_source.job_id
    assert replayed.status == "archived"
    assert replayed.archive_document_id == "doc_recovered_1"
    # The recovery rendered under a fresh identity - it can never replay the
    # artifactless terminal render job of the source.
    assert render_client.render_job_ids == [f"rdr_{replayed.job_id}_pdf"]
    assert f"rdr_{failed_source.job_id}_pdf" not in render_client.render_job_ids
    # Exactly one archived document: the failed original never reached archive,
    # so recovery does not duplicate a client document.
    assert len(archive_client.payloads) == 1
    # The replay reused the retained snapshot verbatim, with explicit clone
    # lineage - the report evidence cannot drift from the original capture.
    source_snapshot = store.get_snapshot_by_job(failed_source.job_id)
    cloned = store.get_snapshot_by_job(replayed.job_id)
    assert cloned.snapshot_payload == source_snapshot.snapshot_payload
    assert cloned.lineage_summary["cloned_from_report_job_id"] == failed_source.job_id
    assert cloned.lineage_summary["cloned_from_snapshot_id"] == source_snapshot.snapshot_id
    # No upstream calls were made for the cloned snapshot, so its per-snapshot
    # call counters are zero and point at the source snapshot's evidence -
    # the lineage endpoint joins calls by snapshot id and must not contradict.
    assert cloned.lineage_summary["call_count"] == 0
    assert cloned.lineage_summary["upstream_evidence"] == "cloned_from_source_snapshot"
    assert (
        cloned.lineage_summary["source_call_count"]
        == (source_snapshot.lineage_summary["call_count"])
    )
    assert (
        cloned.lineage_summary["source_services"]
        == (source_snapshot.lineage_summary["source_services"])
    )


@pytest.mark.asyncio
async def test_artifactless_replay_fails_closed_when_snapshot_missing(tmp_path):
    """The render_artifact_unrecoverable posture promises recovery from the
    retained snapshot. If that snapshot is gone, recollecting current upstream
    state could silently produce different report evidence under a
    failed-work-replay relationship - the replay must refuse instead, before
    any replayed job is created."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    capture = _RecapturingCapture(ledger, store)
    replay_service, render_client, archive_client = _recovery_services(
        ledger, store, capture, snapshot_store=store
    )

    with pytest.raises(InvalidReportJobTransitionError):
        await replay_service.replay_job(
            job_id=failed_source.job_id,
            command=ReportJobReplayRequest(reason="Recover without retained snapshot."),
            caller_context=_caller(),
            idempotency_key="recover-artifactless-missing",
        )

    assert capture.called is False
    assert render_client.render_job_ids == []
    assert archive_client.payloads == []


@pytest.mark.asyncio
async def test_replay_refuses_cross_tenant_and_cross_region_callers(tmp_path):
    """Tenant and region are segregation boundaries: a caller must not be able
    to materialize another tenant's report evidence into a document under its
    own context, and the refusal must look exactly like an unknown job id."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    _create_snapshot_for(store, failed_source)
    replay_service, _render_client, archive_client = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store
    )

    for update in (
        {"tenant_id": "tenant-other"},
        {"region": "EMEA"},
        {"booking_center_code": "HK"},
    ):
        foreign_caller = _caller().model_copy(update=update)
        with pytest.raises(ReportJobNotFoundError):
            await replay_service.replay_job(
                job_id=failed_source.job_id,
                command=ReportJobReplayRequest(reason="Cross-boundary replay."),
                caller_context=foreign_caller,
                idempotency_key="recover-cross-tenant",
            )
    assert archive_client.payloads == []


@pytest.mark.asyncio
async def test_replay_resumes_clone_after_crash_between_collecting_and_data_ready(tmp_path):
    """A crash after the durable collecting_data transition but before
    data_ready must not strand the recovery: retrying the replay with the
    same idempotency key resumes the clone and completes the document."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    _create_snapshot_for(store, failed_source)

    # Simulate the crashed first attempt: the derived job exists under the
    # replay idempotency key and durably reached collecting_data, but the
    # snapshot clone and data_ready never happened.
    crash_key = replay_idempotency_key(
        source_job_id=failed_source.job_id, idempotency_key="recover-after-crash"
    )
    stuck = ledger.create_portfolio_review_job(
        request=portfolio_review_request_from_job(failed_source),
        caller_context=_caller(),
        idempotency_key=crash_key,
    )
    stuck = ledger.mark_collecting_data(
        job_id=stuck.job_id,
        actor=stuck.triggered_by,
        correlation_id=stuck.correlation_id,
        trace_id=stuck.trace_id,
    )
    assert stuck.status == "collecting_data"

    replay_service, _render_client, archive_client = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store
    )
    result = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Retry after crash."),
        caller_context=_caller(),
        idempotency_key="recover-after-crash",
    )

    assert result.replayed_job.job_id == stuck.job_id
    assert result.replayed_job.status == "archived"
    assert len(archive_client.payloads) == 1


@pytest.mark.asyncio
async def test_chained_replay_preserves_root_evidence_pointers(tmp_path):
    """When a replayed job's own artifactless failure is replayed again, the
    second clone must keep pointing at the ROOT snapshot that holds the
    upstream-call rows - not at the intermediate clone, which has none."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    intermediate = _artifactless_failed_source(ledger)
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=intermediate.job_id,
            report_type=intermediate.report_type,
            report_data_contract_version="v1",
            portfolio_scope=intermediate.portfolio_scope,
            as_of_date=intermediate.as_of_date,
            snapshot_payload=_SNAPSHOT_PAYLOAD,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={
                "source_services": ["lotus-core"],
                "call_count": 0,
                "upstream_evidence": "cloned_from_source_snapshot",
                "source_call_count": 4,
                "cloned_from_report_job_id": "rjob_root",
                "cloned_from_snapshot_id": "rsnap_root",
            },
            captured_at=datetime.now(UTC),
            correlation_id=intermediate.correlation_id,
            trace_id=intermediate.trace_id,
        )
    )
    replay_service, _render_client, _archive_client = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store
    )

    result = await replay_service.replay_job(
        job_id=intermediate.job_id,
        command=ReportJobReplayRequest(reason="Second recovery in a chain."),
        caller_context=_caller(),
        idempotency_key="recover-chained",
    )

    cloned = store.get_snapshot_by_job(result.replayed_job.job_id)
    assert cloned.lineage_summary["cloned_from_snapshot_id"] == "rsnap_root"
    assert cloned.lineage_summary["cloned_from_report_job_id"] == "rjob_root"
    assert cloned.lineage_summary["source_call_count"] == 4
    assert cloned.lineage_summary["call_count"] == 0


async def _fail_source_through_artifactless_render(ledger, store, *, fingerprint: str):
    """Drive the source job through the REAL trap: render completes (with a
    fingerprint) but the replayed response carries no artifact bytes, so the
    archive leg fails it as render_artifact_unrecoverable - proving the
    render evidence survives the failure transition."""

    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    source = ledger.create_portfolio_review_job(
        request=_request(output_formats=["pdf"]),
        caller_context=_caller(),
        idempotency_key="source-artifactless-render",
    )
    _create_snapshot_for(store, source)
    source = ledger.mark_collecting_data(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
    )
    source = ledger.mark_data_ready(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
    )
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=_RecordingRenderClient(fingerprint=fingerprint, artifact_base64=None),
        archive_client=_RecordingArchiveClient(),
        snapshot_store=store,
        job_ledger=ledger,
    )
    failed = await render_service.render_for_job(source)
    assert failed.status == "failed"
    assert failed.failure_category == "render_artifact_unrecoverable"
    assert failed.render_bounded_determinism_fingerprint == fingerprint
    return failed


async def _replay_and_read_comparison(ledger, store, failed_source, *, render_client):
    replay_service, _rc, _ac = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store, render_client=render_client
    )
    result = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Fingerprint check."),
        caller_context=_caller(),
        idempotency_key="recover-fingerprint",
    )
    events = [
        event
        for event in ledger.list_status_events(result.replayed_job.job_id)
        if event.event_type == "job_replay_fingerprint_compared"
    ]
    assert len(events) == 1
    return result, events[0]


@pytest.mark.asyncio
async def test_replay_records_matched_fingerprint_comparison(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed = await _fail_source_through_artifactless_render(
        ledger, store, fingerprint="typst-0.14.2:aaaa1111"
    )
    result, event = await _replay_and_read_comparison(
        ledger,
        store,
        failed,
        render_client=_RecordingRenderClient(fingerprint="typst-0.14.2:aaaa1111"),
    )
    assert result.replayed_job.status == "archived"
    assert event.event_payload["outcome"] == "matched"
    assert event.event_payload["source_report_job_id"] == failed.job_id


@pytest.mark.asyncio
async def test_terminal_replay_retry_does_not_inflate_fingerprint_metrics(tmp_path):
    """A same-key retry of a terminal replay re-enters the recorder but the
    comparison event dedupes - the counter must track durable events, not
    HTTP retries, or the derived divergence rate inflates."""

    from app.reporting_metrics import _REPLAY_FINGERPRINT_COMPARISONS_TOTAL

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed = await _fail_source_through_artifactless_render(
        ledger, store, fingerprint="typst-0.14.2:aaaa1111"
    )
    replay_service, _rc, _ac = _recovery_services(
        ledger,
        store,
        _RefusingCapture(),
        snapshot_store=store,
        render_client=_RecordingRenderClient(fingerprint="typst-0.14.2:aaaa1111"),
    )
    counter = _REPLAY_FINGERPRINT_COMPARISONS_TOTAL.labels(outcome="matched", reason="none")
    before = counter._value.get()

    for _ in range(3):
        result = await replay_service.replay_job(
            job_id=failed.job_id,
            command=ReportJobReplayRequest(reason="Fingerprint check."),
            caller_context=_caller(),
            idempotency_key="recover-fingerprint-retry",
        )
        assert result.replayed_job.status == "archived"

    assert counter._value.get() == before + 1.0
    events = [
        event
        for event in ledger.list_status_events(result.replayed_job.job_id)
        if event.event_type == "job_replay_fingerprint_compared"
    ]
    assert len(events) == 1


@pytest.mark.asyncio
async def test_replay_records_divergent_fingerprint_without_failing(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed = await _fail_source_through_artifactless_render(
        ledger, store, fingerprint="typst-0.14.2:aaaa1111"
    )
    result, event = await _replay_and_read_comparison(
        ledger,
        store,
        failed,
        render_client=_RecordingRenderClient(fingerprint="typst-0.14.2:bbbb2222"),
    )
    assert result.replayed_job.status == "archived"
    assert event.event_payload["outcome"] == "diverged"
    assert event.event_payload["reason"] == "same_runtime_fingerprint_mismatch"
    from app.reporting_metrics import _REPLAY_FINGERPRINT_COMPARISONS_TOTAL

    assert (
        _REPLAY_FINGERPRINT_COMPARISONS_TOTAL.labels(
            outcome="diverged", reason="same_runtime_fingerprint_mismatch"
        )._value.get()
        >= 1.0
    )
    assert event.event_payload["source_fingerprint"] == "typst-0.14.2:aaaa1111"
    assert event.event_payload["replayed_fingerprint"] == "typst-0.14.2:bbbb2222"


@pytest.mark.asyncio
async def test_replay_records_incomparable_on_runtime_version_change(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed = await _fail_source_through_artifactless_render(
        ledger, store, fingerprint="typst-0.14.2:aaaa1111"
    )
    result, event = await _replay_and_read_comparison(
        ledger,
        store,
        failed,
        render_client=_RecordingRenderClient(
            fingerprint="typst-0.15.0:cccc3333", runtime_version="0.15.0"
        ),
    )
    assert result.replayed_job.status == "archived"
    assert event.event_payload["outcome"] == "incomparable"
    assert event.event_payload["reason"] == "runtime_engine_differs"


@pytest.mark.asyncio
async def test_replay_records_incomparable_for_metadataless_render_after_archive_failure(
    tmp_path,
):
    """A valid render response may omit every optional metadata field; if the
    archive leg then fails, the durable job_completed event - not the absent
    metadata - proves the render finished, and the comparison records
    incomparable rather than staying silent."""

    class _MetadatalessRenderClient(_RecordingRenderClient):
        async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
            status_code, response = await super().submit_render_package(
                payload, correlation_id, trace_id
            )
            for key in (
                "artifact_sha256",
                "bounded_determinism_fingerprint",
                "runtime_engine",
                "runtime_engine_version",
            ):
                response.pop(key, None)
            return status_code, response

    class _FailingArchiveClient:
        async def archive_document(self, payload, **kwargs):
            return 503, {"detail": "archive unavailable"}

    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = await _fail_source_through_artifactless_render(
        ledger, store, fingerprint="typst-0.14.2:aaaa1111"
    )
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=_MetadatalessRenderClient(),
        archive_client=_FailingArchiveClient(),
        snapshot_store=store,
        job_ledger=ledger,
    )
    replay_service = PortfolioReviewReplayService(
        ledger=ledger,
        capture_service=_RefusingCapture(),
        render_service=render_service,
        snapshot_store=store,
    )

    result = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Metadataless render, archive fails."),
        caller_context=_caller(),
        idempotency_key="recover-metadataless",
    )

    assert result.replayed_job.status == "failed"
    assert result.replayed_job.render_bounded_determinism_fingerprint is None
    events = [
        event
        for event in ledger.list_status_events(result.replayed_job.job_id)
        if event.event_type == "job_replay_fingerprint_compared"
    ]
    assert len(events) == 1
    assert events[0].event_payload["outcome"] == "incomparable"
    assert events[0].event_payload["reason"] == "replayed_fingerprint_missing"


@pytest.mark.asyncio
async def test_replay_records_comparison_when_archive_leg_fails_after_render(tmp_path):
    """The replayed render can complete (fingerprint persisted) and the
    archive leg still fail the job; the comparison judges the render, so it
    must be recorded despite the failed job status."""

    class _FailingArchiveClient:
        async def archive_document(self, payload, **kwargs):
            return 503, {"detail": "archive unavailable"}

    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = await _fail_source_through_artifactless_render(
        ledger, store, fingerprint="typst-0.14.2:aaaa1111"
    )
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=_RecordingRenderClient(fingerprint="typst-0.14.2:aaaa1111"),
        archive_client=_FailingArchiveClient(),
        snapshot_store=store,
        job_ledger=ledger,
    )
    replay_service = PortfolioReviewReplayService(
        ledger=ledger,
        capture_service=_RefusingCapture(),
        render_service=render_service,
        snapshot_store=store,
    )

    result = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Archive will fail."),
        caller_context=_caller(),
        idempotency_key="recover-archive-fails",
    )

    assert result.replayed_job.status == "failed"
    assert result.replayed_job.render_bounded_determinism_fingerprint is not None
    events = [
        event
        for event in ledger.list_status_events(result.replayed_job.job_id)
        if event.event_type == "job_replay_fingerprint_compared"
    ]
    assert len(events) == 1
    assert events[0].event_payload["outcome"] == "matched"


@pytest.mark.asyncio
async def test_replay_records_incomparable_when_replayed_fingerprint_missing(tmp_path):
    """A successful replay render whose response omits the optional
    fingerprint still records an incomparable outcome - silence would be
    indistinguishable from a failed render."""

    class _NoFingerprintRenderClient(_RecordingRenderClient):
        async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
            status_code, response = await super().submit_render_package(
                payload, correlation_id, trace_id
            )
            response.pop("bounded_determinism_fingerprint", None)
            return status_code, response

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed = await _fail_source_through_artifactless_render(
        ledger, store, fingerprint="typst-0.14.2:aaaa1111"
    )
    result, event = await _replay_and_read_comparison(
        ledger, store, failed, render_client=_NoFingerprintRenderClient()
    )
    assert result.replayed_job.status == "archived"
    assert event.event_payload["outcome"] == "incomparable"
    assert event.event_payload["reason"] == "replayed_fingerprint_missing"


@pytest.mark.asyncio
async def test_replay_records_incomparable_when_runtime_identity_missing(tmp_path):
    """Absent runtime metadata must not compare equal as None == None: a
    match claim requires proof both renders ran the same governed runtime."""

    class _NoRuntimeRenderClient(_RecordingRenderClient):
        async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
            status_code, response = await super().submit_render_package(
                payload, correlation_id, trace_id
            )
            response.pop("runtime_engine", None)
            response.pop("runtime_engine_version", None)
            return status_code, response

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    source = ledger.create_portfolio_review_job(
        request=_request(output_formats=["pdf"]),
        caller_context=_caller(),
        idempotency_key="source-no-runtime",
    )
    _create_snapshot_for(store, source)
    source = ledger.mark_collecting_data(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
    )
    source = ledger.mark_data_ready(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
    )
    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    failing_render = PortfolioReviewRenderOrchestrationService(
        render_client=_NoRuntimeRenderClient(artifact_base64=None),
        archive_client=_RecordingArchiveClient(),
        snapshot_store=store,
        job_ledger=ledger,
    )
    failed = await failing_render.render_for_job(source)
    assert failed.failure_category == "render_artifact_unrecoverable"
    assert failed.render_bounded_determinism_fingerprint is not None
    assert failed.render_runtime_engine is None

    result, event = await _replay_and_read_comparison(
        ledger,
        store,
        failed,
        render_client=_NoRuntimeRenderClient(),
    )
    assert result.replayed_job.status == "archived"
    assert event.event_payload["outcome"] == "incomparable"
    assert event.event_payload["reason"] == "runtime_identity_missing"


def test_append_job_event_converges_on_idempotency_key(tmp_path):
    """The duplicate check runs inside the same lock as the insert, so
    same-key appends converge on one event without a pre-scan race."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="atomic-event",
    )
    for _ in range(3):
        ledger.append_job_event(
            job_id=job.job_id,
            event_type="job_replay_fingerprint_compared",
            message="Comparison recorded.",
            event_payload={"outcome": "matched", "source_report_job_id": "rjob_src"},
            event_idempotency_key=f"job_replay_fingerprint_compared:{job.job_id}",
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            skip_if_idempotency_key_exists=True,
        )
    events = [
        event
        for event in ledger.list_status_events(job.job_id)
        if event.event_type == "job_replay_fingerprint_compared"
    ]
    assert len(events) == 1


@pytest.mark.asyncio
async def test_replay_records_incomparable_when_source_fingerprint_missing(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    _create_snapshot_for(store, failed_source)
    result, event = await _replay_and_read_comparison(
        ledger, store, failed_source, render_client=_RecordingRenderClient()
    )
    assert result.replayed_job.status == "archived"
    assert event.event_payload["outcome"] == "incomparable"
    assert event.event_payload["reason"] == "source_fingerprint_missing"


class _FailingRenderClient:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 503, {"detail": "render unavailable"}


@pytest.mark.asyncio
async def test_json_only_replay_records_no_fingerprint_comparison(tmp_path):
    """A json-only replay never renders, so no comparison event exists and
    the recovery completes at data_ready."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    source = ledger.create_portfolio_review_job(
        request=_request(output_formats=["json"]),
        caller_context=_caller(),
        idempotency_key="source-json-artifactless",
    )
    failed_source = ledger.mark_failed(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
        failure_category="render_artifact_unrecoverable",
        failure_message="Artifact only existed in the original render response.",
        retry_eligible=True,
    )
    _create_snapshot_for(store, failed_source)
    replay_service, _rc, _ac = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store
    )

    result = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Recover json output."),
        caller_context=_caller(),
        idempotency_key="recover-json",
    )

    assert result.replayed_job.status == "data_ready"
    assert [
        event.event_type
        for event in ledger.list_status_events(result.replayed_job.job_id)
        if event.event_type == "job_replay_fingerprint_compared"
    ] == []


@pytest.mark.asyncio
async def test_failed_replay_render_records_no_fingerprint_comparison(tmp_path):
    """When the replayed render itself fails there is no fingerprint to
    compare; the job's own failure posture tells the story and no comparison
    event is recorded."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    _create_snapshot_for(store, failed_source)
    replay_service, _rc, _ac = _recovery_services(
        ledger,
        store,
        _RefusingCapture(),
        snapshot_store=store,
        render_client=_FailingRenderClient(),
    )

    result = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Render will fail."),
        caller_context=_caller(),
        idempotency_key="recover-render-fails",
    )

    assert result.replayed_job.status == "failed"
    assert [
        event.event_type
        for event in ledger.list_status_events(result.replayed_job.job_id)
        if event.event_type == "job_replay_fingerprint_compared"
    ] == []


@pytest.mark.asyncio
async def test_archive_execution_failure_recovers_through_replay(tmp_path):
    """An unclassified archive fault (generic 500) is retryable by design:
    archive ingest is idempotent by arch_{render_job_id}, so the replay
    recovers end-to-end once archive is healthy, with exactly one archived
    document (issue #211)."""

    class _Failing500ArchiveClient:
        async def archive_document(self, payload, **kwargs):
            return 500, {"detail": "unexpected archive fault"}

    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    source = ledger.create_portfolio_review_job(
        request=_request(output_formats=["pdf"]),
        caller_context=_caller(),
        idempotency_key="source-archive-500",
    )
    _create_snapshot_for(store, source)
    source = ledger.mark_collecting_data(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
    )
    source = ledger.mark_data_ready(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
    )
    failing_service = PortfolioReviewRenderOrchestrationService(
        render_client=_RecordingRenderClient(),
        archive_client=_Failing500ArchiveClient(),
        snapshot_store=store,
        job_ledger=ledger,
    )
    failed = await failing_service.render_for_job(source)
    assert failed.failure_category == "archive_execution_failed"
    assert failed.retry_eligible is True

    replay_service, _rc, archive_client = _recovery_services(
        ledger, store, _RecapturingCapture(ledger, store), snapshot_store=store
    )
    result = await replay_service.replay_job(
        job_id=failed.job_id,
        command=ReportJobReplayRequest(reason="Archive recovered."),
        caller_context=_caller(),
        idempotency_key="recover-archive-500",
    )

    assert result.replayed_job.status == "archived"
    assert len(archive_client.payloads) == 1


class _PurgedSourceSnapshotStore:
    """Store view where the source job's snapshot has been retention-purged."""

    def __init__(self, inner: ReportInputSnapshotStore, purged_job_id: str) -> None:
        self._inner = inner
        self._purged_job_id = purged_job_id

    def get_snapshot_by_job(self, report_job_id: str):
        if report_job_id == self._purged_job_id:
            from app.reporting_lineage.store import ReportInputSnapshotNotFoundError

            raise ReportInputSnapshotNotFoundError("report_input_snapshot_not_found")
        return self._inner.get_snapshot_by_job(report_job_id)

    def create_snapshot(self, request):
        return self._inner.create_snapshot(request)


@pytest.mark.asyncio
async def test_completed_replay_retry_stays_idempotent_after_snapshot_purge(tmp_path):
    """A same-key retry of an already completed replay must return the
    existing derived job even after the source snapshot ages out of
    retention - the snapshot is required only while collection still has
    to happen."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    _create_snapshot_for(store, failed_source)
    replay_service, _render_client, archive_client = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store
    )
    first = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Recover artifactless render."),
        caller_context=_caller(),
        idempotency_key="recover-then-purge",
    )
    assert first.replayed_job.status == "archived"

    purged_store = _PurgedSourceSnapshotStore(store, failed_source.job_id)
    retry_service = PortfolioReviewReplayService(
        ledger=ledger,
        capture_service=_RefusingCapture(),
        render_service=replay_service._render_service,
        snapshot_store=purged_store,
    )
    retried = await retry_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Recover artifactless render."),
        caller_context=_caller(),
        idempotency_key="recover-then-purge",
    )

    assert retried.replayed_job.job_id == first.replayed_job.job_id
    assert retried.replayed_job.status == "archived"
    assert len(archive_client.payloads) == 1


@pytest.mark.asyncio
async def test_clone_event_not_duplicated_when_resuming_after_event_commit(tmp_path):
    """The event idempotency key is only a non-unique index, so a crash after
    the clone event committed but before data_ready must not produce a second
    audit event claiming the snapshot was cloned again."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    _create_snapshot_for(store, failed_source)
    source_snapshot = store.get_snapshot_by_job(failed_source.job_id)

    crash_key = replay_idempotency_key(
        source_job_id=failed_source.job_id, idempotency_key="resume-after-event"
    )
    stuck = ledger.create_portfolio_review_job(
        request=portfolio_review_request_from_job(failed_source),
        caller_context=_caller(),
        idempotency_key=crash_key,
    )
    stuck = ledger.mark_collecting_data(
        job_id=stuck.job_id,
        actor=stuck.triggered_by,
        correlation_id=stuck.correlation_id,
        trace_id=stuck.trace_id,
    )
    # The crashed attempt persisted the cloned snapshot AND its event.
    _create_snapshot_for(store, stuck)
    cloned_snapshot = store.get_snapshot_by_job(stuck.job_id)
    ledger.append_job_event(
        job_id=stuck.job_id,
        event_type="job_replay_snapshot_cloned",
        message="Replay reused retained input snapshot.",
        event_payload={
            "source_snapshot_id": source_snapshot.snapshot_id,
            "cloned_snapshot_id": cloned_snapshot.snapshot_id,
            "replayed_job_id": stuck.job_id,
        },
        event_idempotency_key=f"job_replay_snapshot_cloned:{stuck.job_id}",
        actor=stuck.triggered_by,
        correlation_id=stuck.correlation_id,
        trace_id=stuck.trace_id,
    )

    replay_service, _render_client, _archive_client = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store
    )
    result = await replay_service.replay_job(
        job_id=failed_source.job_id,
        command=ReportJobReplayRequest(reason="Retry after crash."),
        caller_context=_caller(),
        idempotency_key="resume-after-event",
    )

    assert result.replayed_job.status == "archived"
    clone_events = [
        event
        for event in ledger.list_status_events(stuck.job_id)
        if event.event_type == "job_replay_snapshot_cloned"
    ]
    assert len(clone_events) == 1


def test_regenerate_rejects_non_portfolio_review_report_types(tmp_path):
    """Regenerate recreates a portfolio-review order; an archived job of any
    other family must be refused, or its replacement document would morph
    report types (same class as the replay guard)."""

    from app.reporting_render.regenerate_service import _assert_regenerate_eligible

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(output_formats=["pdf"]),
        caller_context=_caller(),
        idempotency_key="source-regenerate-guard",
    )
    archived_like = job.model_copy(
        update={
            "status": "archived",
            "render_job_id": f"rdr_{job.job_id}_pdf",
            "archive_document_id": "doc_original",
        }
    )
    _assert_regenerate_eligible(archived_like)
    for report_type in ("proof_pack", "outcome_review", "rebalance_wave"):
        with pytest.raises(InvalidReportJobTransitionError):
            _assert_regenerate_eligible(
                archived_like.model_copy(update={"report_type": report_type})
            )


def test_replay_rejects_non_portfolio_review_report_types(tmp_path):
    """The replay command recreates a portfolio-review order, so replaying any
    other report type would silently morph it - eligibility must refuse."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    for report_type in ("proof_pack", "outcome_review", "rebalance_wave"):
        morphed = failed_source.model_copy(update={"report_type": report_type})
        with pytest.raises(InvalidReportJobTransitionError):
            assert_replay_eligible(morphed)
