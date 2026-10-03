"""Job execution must propagate accepted identity and restore its caller scope."""

import asyncio

import pytest

from app.observability import correlation_id_var, propagation_headers, request_id_var, trace_id_var
from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_lineage.capture_service import (
    PortfolioReviewInputCaptureError,
    ReportingReadPortfolioReviewInputProvider,
)
from app.reporting_render.waiting import RenderWaiting
from tests.unit.reporting_jobs.test_report_job_execution import _job


@pytest.fixture
def worker_pass_context():
    tokens = [
        (correlation_id_var, correlation_id_var.set("synthetic-worker-pass-correlation")),
        (trace_id_var, trace_id_var.set("a" * 32)),
        (request_id_var, request_id_var.set("report_job_worker_pass_17")),
    ]
    try:
        yield propagation_headers()
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def _assert_job_scope(job):
    assert propagation_headers() == {
        "X-Correlation-Id": job.correlation_id,
        "X-Trace-Id": job.trace_id,
        "X-Request-Id": "report_job_worker_pass_17",
        "traceparent": f"00-{job.trace_id}-0000000000000001-01",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["success", "refusal", "exception", "cancellation", "waiting", "render-exception"]
)
async def test_execution_restores_pass_scope_and_isolates_adjacent_jobs(worker_pass_context, mode):
    observed = []

    class Capture:
        async def capture_for_job(self, job):
            _assert_job_scope(job)
            observed.append(("capture", job.job_id, job.tenant_id))
            if mode == "exception":
                raise RuntimeError("synthetic-capture-error")
            if mode == "cancellation":
                raise asyncio.CancelledError()
            return job.model_copy(
                update={"status": "failed" if mode == "refusal" else "data_ready"}
            )

    class Render:
        async def render_for_job(self, job):
            _assert_job_scope(job)
            observed.append(("render", job.job_id, job.tenant_id))
            if mode == "render-exception":
                raise RuntimeError("synthetic-render-error")
            return (
                RenderWaiting(job=job)
                if mode == "waiting"
                else job.model_copy(update={"status": "archived"})
            )

    for suffix in ("1", "2"):
        job = _job().model_copy(
            update={
                "job_id": f"synthetic-job-{suffix}",
                "tenant_id": f"synthetic-tenant-{suffix}",
                "correlation_id": f"synthetic-job-correlation-{suffix}",
                "trace_id": suffix * 32,
            }
        )

        class Ledger:
            def get_job(self, job_id):
                assert job_id == job.job_id
                assert propagation_headers() == worker_pass_context
                return job

        executor = ReportJobExecutionService(
            report_job_ledger=Ledger(), capture_service=Capture(), render_service=Render()
        )
        if mode in {"exception", "cancellation", "render-exception"}:
            with pytest.raises(asyncio.CancelledError if mode == "cancellation" else RuntimeError):
                await executor.execute_job(job_id=job.job_id)
        else:
            result = await executor.execute_job(job_id=job.job_id)
            assert result.waiting_on_owner is (mode == "waiting")
            assert (
                result.job.status
                == {"success": "archived", "refusal": "failed", "waiting": "data_ready"}[mode]
            )
        assert propagation_headers() == worker_pass_context
    expected = [
        ("capture", f"synthetic-job-{suffix}", f"synthetic-tenant-{suffix}")
        for suffix in ("1", "2")
    ]
    if mode in {"success", "waiting", "render-exception"}:
        expected = [
            entry
            for suffix in ("1", "2")
            for entry in (
                ("capture", f"synthetic-job-{suffix}", f"synthetic-tenant-{suffix}"),
                ("render", f"synthetic-job-{suffix}", f"synthetic-tenant-{suffix}"),
            )
        ]
    assert observed == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
async def test_direct_source_collection_binds_job_context_for_api_recovery(
    worker_pass_context, failure
):
    job = _job().model_copy(
        update={"correlation_id": "synthetic-api-accepted", "trace_id": "b" * 32}
    )

    class ReadService:
        async def get_portfolio_review(self, **kwargs):
            _assert_job_scope(job)
            assert kwargs["correlation_id"] == job.correlation_id
            assert kwargs["admitted_tenant_id"] == job.tenant_id
            if failure:
                raise failure()
            return {"portfolio_id": "SYNTHETIC_CONTEXT", "as_of_date": "2026-04-10"}

    provider = ReportingReadPortfolioReviewInputProvider(read_service=ReadService())
    if failure:
        with pytest.raises(
            asyncio.CancelledError
            if failure is asyncio.CancelledError
            else PortfolioReviewInputCaptureError
        ) as caught:
            await provider.collect_for_job(job)
        if failure is RuntimeError:
            assert type(caught.value.original_error) is RuntimeError
    else:
        capture = await provider.collect_for_job(job)
        assert capture.snapshot_payload["portfolio_id"] == "SYNTHETIC_CONTEXT"
    assert propagation_headers() == worker_pass_context


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["correlation_id", "trace_id"])
@pytest.mark.parametrize("missing", ["", " ", None])
async def test_missing_persisted_identity_refuses_before_source_calls(
    worker_pass_context, field, missing
):
    job = _job().model_copy(update={field: missing})
    reached = []

    class Ledger:
        def get_job(self, job_id):
            return job

    class Stage:
        async def capture_for_job(self, job):
            reached.append("capture")
            return job

        async def render_for_job(self, job):
            reached.append("render")
            return job

        async def get_portfolio_review(self, **kwargs):
            reached.append("source")
            return {}

    stage = Stage()
    executor = ReportJobExecutionService(
        report_job_ledger=Ledger(), capture_service=stage, render_service=stage
    )
    with pytest.raises(ValueError, match="^propagation_identity_missing$"):
        await executor.execute_job(job_id=job.job_id)
    with pytest.raises(ValueError, match="^propagation_identity_missing$"):
        await ReportingReadPortfolioReviewInputProvider(read_service=stage).collect_for_job(job)
    assert reached == []
    assert propagation_headers() == worker_pass_context


@pytest.mark.asyncio
async def test_concurrent_nested_jobs_restore_pass_scope_after_await(worker_pass_context):
    arrived = 0
    both_waiting = asyncio.Event()

    async def run(suffix):
        job = _job().model_copy(
            update={"correlation_id": f"accepted-{suffix}", "trace_id": suffix * 32}
        )

        class ReadService:
            async def get_portfolio_review(self, **kwargs):
                nonlocal arrived
                _assert_job_scope(job)
                arrived += 1
                if arrived == 2:
                    both_waiting.set()
                await asyncio.wait_for(both_waiting.wait(), timeout=5)
                await asyncio.sleep(0)
                _assert_job_scope(job)
                return {"portfolio_id": suffix, "as_of_date": "2026-04-10"}

        provider = ReportingReadPortfolioReviewInputProvider(read_service=ReadService())

        class Capture:
            async def capture_for_job(self, job):
                await provider.collect_for_job(job)
                _assert_job_scope(job)
                return job.model_copy(update={"status": "failed"})

        class Ledger:
            def get_job(self, job_id):
                return job

        result = await ReportJobExecutionService(
            report_job_ledger=Ledger(), capture_service=Capture(), render_service=None
        ).execute_job(job_id=job.job_id)
        assert result.job.correlation_id == job.correlation_id
        assert propagation_headers() == worker_pass_context

    await asyncio.gather(run("1"), run("2"))
    assert propagation_headers() == worker_pass_context


@pytest.mark.asyncio
async def test_task_cancellation_during_source_await_restores_nested_scope(worker_pass_context):
    waiting = asyncio.Event()
    restored = []
    job = _job().model_copy(update={"correlation_id": "cancelled-job", "trace_id": "c" * 32})

    class ReadService:
        async def get_portfolio_review(self, **kwargs):
            _assert_job_scope(job)
            waiting.set()
            await asyncio.Event().wait()

    provider = ReportingReadPortfolioReviewInputProvider(read_service=ReadService())

    class Capture:
        async def capture_for_job(self, job):
            try:
                await provider.collect_for_job(job)
            finally:
                _assert_job_scope(job)

    class Ledger:
        def get_job(self, job_id):
            return job

    async def run():
        try:
            await ReportJobExecutionService(
                report_job_ledger=Ledger(), capture_service=Capture(), render_service=None
            ).execute_job(job_id=job.job_id)
        finally:
            restored.append(propagation_headers())

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    assert restored == [worker_pass_context]
    assert propagation_headers() == worker_pass_context
