import pytest

from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobReplayRequest,
)
from app.reporting_render.replay_service import (
    PortfolioReviewReplayService,
    assert_replay_eligible,
    get_portfolio_review_replay_service,
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

    service = get_portfolio_review_replay_service()

    assert service._ledger is ledger
    assert service._capture_service is capture_service
    assert service._render_service is render_service
