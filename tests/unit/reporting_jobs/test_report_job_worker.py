from datetime import UTC, date, datetime

import pytest

from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_jobs.work_queue import ReportJobWorkItem, ReportJobWorkRetryPolicy
from app.reporting_jobs.worker import ReportJobWorker


def _work_item(*, status="leased", attempt_count=1):
    now = datetime(2026, 8, 23, tzinfo=UTC)
    return ReportJobWorkItem(
        work_item_id="rwork_1",
        report_job_id="rjob_1",
        status=status,
        attempt_count=attempt_count,
        available_at=now,
        lease_owner="worker-1" if status == "leased" else None,
        lease_token="lease-1" if status == "leased" else None,
        lease_acquired_at=now if status == "leased" else None,
        lease_expires_at=now if status == "leased" else None,
        created_at=now,
        updated_at=now,
    )


def _job(*, status="archived", failure_category=None, output_formats=None):
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
        failure_category=failure_category,
        current_step=status,
        retry_eligible=False,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
        correlation_id="corr-1",
        trace_id="trace-1",
    )


class _Ledger:
    def __init__(self, items=None):
        self.items = [_work_item()] if items is None else items
        self.failed = []
        self.completed = []

    def claim_work_items(self, *, worker_id, limit, lease_seconds):
        assert worker_id == "worker-1"
        assert limit == 5
        assert lease_seconds == 60
        return self.items

    def complete_work_item(self, *, work_item_id, lease_token):
        self.completed.append((work_item_id, lease_token))
        return _work_item(status="completed")

    def fail_work_item(self, **kwargs):
        self.failed.append(kwargs)
        return _work_item(status="retry_pending")


class _Executor:
    def __init__(self, result=None, error=None):
        self.result = result or _job()
        self.error = error

    async def execute_job(self, *, job_id):
        assert job_id == "rjob_1"
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "archived", "failed", "cancelled"])
async def test_worker_completes_work_for_truthful_terminal_job_status(status):
    ledger = _Ledger()
    result = await ReportJobWorker(
        work_ledger=ledger,
        execution_service=_Executor(result=_job(status=status)),
    ).run_once(worker_id="worker-1", max_items=5, lease_seconds=60)

    assert result.claimed_count == 1
    assert result.completed_count == 1
    assert result.retry_pending_count == 0
    assert ledger.completed == [("rwork_1", "lease-1")]


@pytest.mark.asyncio
async def test_worker_completes_data_ready_non_pdf_job():
    ledger = _Ledger()
    result = await ReportJobWorker(
        work_ledger=ledger,
        execution_service=_Executor(result=_job(status="data_ready", output_formats=["json"])),
    ).run_once(worker_id="worker-1", max_items=5, lease_seconds=60)

    assert result.completed_count == 1
    assert ledger.completed == [("rwork_1", "lease-1")]


@pytest.mark.asyncio
async def test_worker_retries_unexpected_execution_failure_without_claiming_job_success():
    ledger = _Ledger()
    policy = ReportJobWorkRetryPolicy(max_attempts=4)
    result = await ReportJobWorker(
        work_ledger=ledger,
        execution_service=_Executor(error=RuntimeError("connection reset")),
        retry_policy=policy,
    ).run_once(worker_id="worker-1", max_items=5, lease_seconds=60)

    assert result.retry_pending_count == 1
    assert result.outcomes[0].job_status == "unknown"
    assert ledger.completed == []
    assert ledger.failed[0]["error_category"] == "report_job_worker_execution_failed"
    assert ledger.failed[0]["retry_policy"] is policy


@pytest.mark.asyncio
async def test_worker_retries_non_terminal_pipeline_state():
    ledger = _Ledger()
    result = await ReportJobWorker(
        work_ledger=ledger,
        execution_service=_Executor(result=_job(status="rendering")),
    ).run_once(worker_id="worker-1", max_items=5, lease_seconds=60)

    assert result.retry_pending_count == 1
    assert result.outcomes[0].job_status == "rendering"
    assert ledger.failed[0]["error_category"] == "report_job_worker_incomplete"


@pytest.mark.asyncio
async def test_worker_handles_empty_queue_without_side_effects():
    ledger = _Ledger(items=[])
    result = await ReportJobWorker(
        work_ledger=ledger,
        execution_service=_Executor(),
    ).run_once(worker_id="worker-1", max_items=5, lease_seconds=60)

    assert result.claimed_count == 0
    assert result.outcomes == []


@pytest.mark.asyncio
async def test_worker_rejects_unleased_work_before_report_execution():
    ledger = _Ledger(items=[_work_item(status="pending")])
    executor = _Executor()

    with pytest.raises(RuntimeError, match="report_job_work_item_missing_lease_token"):
        await ReportJobWorker(
            work_ledger=ledger,
            execution_service=executor,
        ).run_once(worker_id="worker-1", max_items=5, lease_seconds=60)

    assert ledger.completed == []
    assert ledger.failed == []
