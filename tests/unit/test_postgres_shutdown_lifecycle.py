import asyncio
import time

import pytest
from fastapi import FastAPI

from app import main
from app.postgres import PostgresProviderDrainIncomplete
from app.reporting_jobs import process


@pytest.mark.parametrize("deadline", [None, 123.0])
@pytest.mark.parametrize("error", [None, RuntimeError("request failed"), asyncio.CancelledError()])
async def test_api_lifespan_drains_on_normal_error_and_cancellation(monkeypatch, deadline, error):
    application = FastAPI()
    if deadline is not None:
        application.state.postgres_shutdown_deadline = deadline
    calls = []

    def close(**kwargs):
        assert application.state.is_draining
        calls.append(kwargs)

    monkeypatch.setattr(main, "close_postgres_connection_provider", close)

    async def run():
        async with main._app_lifespan(application):
            assert not application.state.is_draining
            if error is not None:
                raise error

    if error is None:
        await run()
    else:
        with pytest.raises(type(error)):
            await run()
    assert calls == ([{}] if deadline is None else [{"deadline": deadline}])
    assert application.state.is_draining


async def test_api_shutdown_reports_incomplete_drain(monkeypatch):
    def close():
        raise PostgresProviderDrainIncomplete("still charged")

    monkeypatch.setattr(main, "close_postgres_connection_provider", close)
    application = FastAPI()
    with pytest.raises(PostgresProviderDrainIncomplete, match="still charged"):
        async with main._app_lifespan(application):
            pass
    assert application.state.is_draining


@pytest.mark.parametrize("deadline", [None, 123.0])
@pytest.mark.parametrize("error", [None, RuntimeError("worker failed"), asyncio.CancelledError()])
async def test_worker_cleanup_preserves_absolute_owner_deadline(monkeypatch, deadline, error):
    calls = []
    monkeypatch.setattr(process, "build_archive_lineage_maintenance", lambda source: None)
    monkeypatch.setattr(
        process, "close_postgres_connection_provider", lambda **kw: calls.append(kw)
    )

    async def run(self, **kwargs):
        if error is not None:
            raise error

    monkeypatch.setattr(process.ReportJobWorkerProcess, "run", run)
    if error is None:
        await process.run_report_job_worker_process(worker=object(), shutdown_deadline=deadline)
    else:
        with pytest.raises(type(error)):
            await process.run_report_job_worker_process(worker=object(), shutdown_deadline=deadline)
    assert calls == ([{}] if deadline is None else [{"deadline": deadline}])


async def test_worker_construction_failure_still_cleans_up(monkeypatch):
    calls = []

    def build(**kwargs):
        raise RuntimeError("build failed")

    monkeypatch.setattr(process, "get_report_job_worker", build)
    monkeypatch.setattr(
        process, "close_postgres_connection_provider", lambda **kw: calls.append(kw)
    )
    deadline = time.monotonic() + 1
    with pytest.raises(RuntimeError, match="build failed"):
        await process.run_report_job_worker_process(shutdown_deadline=deadline)
    assert calls == [{"deadline": deadline}]


async def test_worker_shutdown_reports_incomplete_drain(monkeypatch):
    monkeypatch.setattr(process, "build_archive_lineage_maintenance", lambda source: None)

    async def run(self, **kwargs):
        pass

    def close():
        raise PostgresProviderDrainIncomplete("still charged")

    monkeypatch.setattr(process.ReportJobWorkerProcess, "run", run)
    monkeypatch.setattr(process, "close_postgres_connection_provider", close)
    with pytest.raises(PostgresProviderDrainIncomplete, match="still charged"):
        await process.run_report_job_worker_process(worker=object())
