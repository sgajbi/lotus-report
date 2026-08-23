from datetime import UTC, date, datetime

import pytest

from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_jobs.models import ReportJobLedgerRecord


def _job(*, status: str = "accepted", output_formats: list[str] | None = None):
    now = datetime(2026, 8, 23, tzinfo=UTC)
    return ReportJobLedgerRecord(
        request_id="rrq_1",
        job_id="rjob_1",
        report_type="portfolio_review",
        portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        requested_output_formats=output_formats or ["pdf"],
        as_of_date=date(2026, 4, 10),
        options={},
        trigger_type="user",
        triggered_by="advisor-1",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        idempotency_key="report-1",
        request_hash="sha256:request",
        status=status,
        current_step=status,
        retry_eligible=False,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
        correlation_id="corr-1",
        trace_id="trace-1",
    )


class _Ledger:
    def __init__(self, job):
        self.job = job

    def get_job(self, job_id):
        assert job_id == self.job.job_id
        return self.job


class _Capture:
    def __init__(self):
        self.calls = []

    async def capture_for_job(self, job):
        self.calls.append(job.status)
        return job.model_copy(update={"status": "data_ready", "current_step": "data_ready"})


class _Render:
    def __init__(self):
        self.calls = []

    async def render_for_job(self, job):
        self.calls.append(job.status)
        return job.model_copy(update={"status": "archived", "current_step": "archived"})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "collecting_data"])
async def test_executor_advances_capture_then_render(status):
    capture = _Capture()
    render = _Render()
    result = await ReportJobExecutionService(
        report_job_ledger=_Ledger(_job(status=status)),
        capture_service=capture,
        render_service=render,
    ).execute_job(job_id="rjob_1")

    assert capture.calls == [status]
    assert render.calls == ["data_ready"]
    assert result.status == "archived"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["data_ready", "rendering", "completed", "archiving"])
async def test_executor_advances_data_ready_pdf_job(status):
    capture = _Capture()
    render = _Render()
    result = await ReportJobExecutionService(
        report_job_ledger=_Ledger(_job(status=status)),
        capture_service=capture,
        render_service=render,
    ).execute_job(job_id="rjob_1")

    assert capture.calls == []
    assert render.calls == [status]
    assert result.status == "archived"


@pytest.mark.asyncio
async def test_executor_leaves_terminal_job_unchanged():
    capture = _Capture()
    render = _Render()
    terminal = _job(status="failed")
    result = await ReportJobExecutionService(
        report_job_ledger=_Ledger(terminal),
        capture_service=capture,
        render_service=render,
    ).execute_job(job_id="rjob_1")

    assert result is terminal
    assert capture.calls == []
    assert render.calls == []
