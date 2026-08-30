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
    archived = _failed_job(ledger)
    archived_with_doc = archived.model_copy(update={"archive_document_id": "doc_existing"})

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

    def __init__(self) -> None:
        self.render_job_ids: list[str] = []

    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        self.render_job_ids.append(payload["render_job_id"])
        return 201, {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": "portfolio-review",
            "template_version": "v1",
            "artifact_sha256": "sha256:recovered-artifact",
            "artifact_base64": "JVBERi0xLjQKJQ==",
        }


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


def _recovery_services(ledger, store, capture_service, *, snapshot_store):
    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    render_client = _RecordingRenderClient()
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


def test_replay_rejects_non_portfolio_review_report_types(tmp_path):
    """The replay command recreates a portfolio-review order, so replaying any
    other report type would silently morph it - eligibility must refuse."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    failed_source = _artifactless_failed_source(ledger)
    for report_type in ("proof_pack", "outcome_review", "rebalance_wave"):
        morphed = failed_source.model_copy(update={"report_type": report_type})
        with pytest.raises(InvalidReportJobTransitionError):
            assert_replay_eligible(morphed)
