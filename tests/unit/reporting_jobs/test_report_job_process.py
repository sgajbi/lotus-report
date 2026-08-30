from __future__ import annotations

import pytest

import app.reporting_jobs.process as process_module
from app.config import Settings
from app.reporting_jobs.process import (
    ReportJobWorkerProcess,
    ReportJobWorkerProcessConfig,
    report_job_worker_config_from_settings,
    run_report_job_worker_process,
)
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
from app.reporting_jobs.worker import ReportJobWorkerRunResult


class _Worker:
    def __init__(self, *, error: Exception | None = None):
        self.calls = []
        self.error = error

    async def run_once(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return ReportJobWorkerRunResult(
            worker_id=kwargs["worker_id"],
            claimed_count=2,
            completed_count=1,
            retry_pending_count=1,
            failed_count=0,
        )


def _config():
    return ReportJobWorkerProcessConfig(
        worker_id="report-worker-1",
        interval_seconds=0.5,
        max_items_per_pass=7,
        lease_seconds=90,
        retry_policy=ReportJobWorkRetryPolicy(
            max_attempts=4,
            base_delay_seconds=3,
            max_delay_seconds=45,
        ),
    )


def test_report_job_worker_config_maps_governed_settings():
    source = Settings(
        REPORT_JOB_WORKER_ID="report-worker-configured",
        REPORT_JOB_WORKER_INTERVAL_SECONDS=2,
        REPORT_JOB_WORKER_MAX_ITEMS_PER_PASS=11,
        REPORT_JOB_WORKER_LEASE_SECONDS=120,
        REPORT_JOB_WORKER_MAX_ATTEMPTS=5,
        REPORT_JOB_WORKER_RETRY_BASE_SECONDS=4,
        REPORT_JOB_WORKER_RETRY_MAX_SECONDS=80,
    )

    config = report_job_worker_config_from_settings(source)

    assert config.worker_id == "report-worker-configured"
    assert config.interval_seconds == 2
    assert config.max_items_per_pass == 11
    assert config.lease_seconds == 120
    assert config.retry_policy == ReportJobWorkRetryPolicy(
        max_attempts=5,
        base_delay_seconds=4,
        max_delay_seconds=80,
    )


@pytest.mark.asyncio
async def test_process_runs_bounded_passes_and_records_metrics(monkeypatch):
    worker = _Worker()
    sleeps = []
    metrics = []

    async def _sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(
        "app.reporting_jobs.process.record_report_job_worker_metrics",
        lambda **kwargs: metrics.append(kwargs),
    )
    await ReportJobWorkerProcess(
        worker=worker,
        config=_config(),
        sleep=_sleep,
    ).run(max_iterations=2)

    assert len(worker.calls) == 2
    assert worker.calls[0] == {
        "worker_id": "report-worker-1",
        "max_items": 7,
        "lease_seconds": 90,
    }
    assert sleeps == [0.5]
    assert metrics[0]["claimed_count"] == 2
    assert metrics[0]["completed_count"] == 1


@pytest.mark.asyncio
async def test_process_records_runtime_failure_and_propagates(monkeypatch):
    metrics = []
    monkeypatch.setattr(
        "app.reporting_jobs.process.record_report_job_worker_metrics",
        lambda **kwargs: metrics.append(kwargs),
    )
    process = ReportJobWorkerProcess(
        worker=_Worker(error=RuntimeError("database unavailable")),
        config=_config(),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await process.run(max_iterations=1)

    assert metrics == [
        {
            "claimed_count": 0,
            "completed_count": 0,
            "retry_pending_count": 0,
            "failed_count": 0,
            "status": "failed",
            "failure_category": "report_job_worker_runtime_error",
            "duration_seconds": metrics[0]["duration_seconds"],
        }
    ]


@pytest.mark.asyncio
async def test_run_process_uses_injected_worker():
    worker = _Worker()
    source = Settings(REPORT_JOB_WORKER_INTERVAL_SECONDS=0.1)

    await run_report_job_worker_process(
        worker=worker,
        source_settings=source,
        max_iterations=1,
    )

    assert len(worker.calls) == 1


@pytest.mark.asyncio
async def test_process_stop_prevents_another_worker_pass():
    worker = _Worker()
    process = ReportJobWorkerProcess(worker=worker, config=_config())

    process.stop()
    await process.run(max_iterations=3)

    assert worker.calls == []


@pytest.mark.asyncio
async def test_run_process_builds_worker_with_governed_retry_policy(monkeypatch):
    worker = _Worker()
    policies = []
    monkeypatch.setattr(
        process_module,
        "get_report_job_worker",
        lambda *, retry_policy: policies.append(retry_policy) or worker,
    )

    await run_report_job_worker_process(
        source_settings=Settings(REPORT_JOB_WORKER_MAX_ATTEMPTS=9),
        max_iterations=1,
    )

    assert policies == [ReportJobWorkRetryPolicy(max_attempts=9)]
    assert len(worker.calls) == 1


@pytest.mark.parametrize(
    ("arguments", "expected_iterations"),
    [(["report-job-worker", "--once"], 1), (["report-job-worker", "--max-iterations", "3"], 3)],
)
def test_worker_cli_maps_bounded_runtime_arguments(
    monkeypatch,
    arguments,
    expected_iterations,
):
    invocations = []
    monkeypatch.setattr("sys.argv", arguments)
    monkeypatch.setattr(process_module, "setup_logging", lambda: invocations.append("logging"))
    monkeypatch.setattr(
        process_module,
        "start_worker_metrics_server",
        lambda: invocations.append("metrics"),
    )

    async def _run(*, max_iterations):
        invocations.append(max_iterations)

    monkeypatch.setattr(process_module, "run_report_job_worker_process", _run)

    process_module.main()

    assert invocations == ["logging", "metrics", expected_iterations]


def test_worker_metrics_server_starts_on_governed_port(monkeypatch):
    """The canonical async lifecycle paths record their counters in THIS
    process; without the exporter every documented alert on them stays
    silently zero (PR #208 review)."""

    import app.reporting_jobs.process as process_module

    captured: list[int] = []
    monkeypatch.setattr("prometheus_client.start_http_server", lambda port: captured.append(port))
    monkeypatch.setattr(
        process_module.settings, "report_job_worker_metrics_port", 9309, raising=False
    )

    process_module.start_worker_metrics_server()
    assert captured == [9309]

    process_module.start_worker_metrics_server(port=9310)
    assert captured == [9309, 9310]
