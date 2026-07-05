from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.report_batch_orchestrator import service as batch_service


class _DependencyCapture:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def test_get_report_batch_ledger_returns_configured_ledger(monkeypatch) -> None:
    provider = _DependencyCapture(label="shared-provider")
    monkeypatch.setattr(
        batch_service,
        "PostgresReportBatchLedger",
        lambda **kwargs: _DependencyCapture(**kwargs),
    )
    monkeypatch.setattr(batch_service, "get_postgres_connection_provider", lambda: provider)
    batch_service.get_report_batch_ledger.cache_clear()

    ledger = batch_service.get_report_batch_ledger()

    assert isinstance(ledger, _DependencyCapture)
    assert ledger.kwargs["connection_provider"] is provider


def test_get_report_batch_worker_wires_dispatch_and_execution_dependencies(monkeypatch) -> None:
    batch_service.get_report_batch_ledger.cache_clear()
    batch_ledger = _DependencyCapture(label="batch-ledger")
    job_ledger = _DependencyCapture(label="job-ledger")
    capture_service = _DependencyCapture(label="capture-service")
    render_service = _DependencyCapture(label="render-service")
    created = {}

    monkeypatch.setattr(batch_service, "get_report_batch_ledger", lambda: batch_ledger)
    monkeypatch.setattr(batch_service, "get_report_job_ledger", lambda: job_ledger)
    monkeypatch.setattr(
        batch_service,
        "get_portfolio_review_snapshot_capture_service",
        lambda: capture_service,
    )
    monkeypatch.setattr(
        batch_service,
        "get_portfolio_review_render_orchestration_service",
        lambda: render_service,
    )
    monkeypatch.setattr(
        batch_service,
        "ReportBatchDispatcher",
        lambda **kwargs: created.setdefault("dispatcher", _DependencyCapture(**kwargs)),
    )
    monkeypatch.setattr(
        batch_service,
        "ReportBatchExecutionService",
        lambda **kwargs: created.setdefault("execution", _DependencyCapture(**kwargs)),
    )
    monkeypatch.setattr(
        batch_service,
        "ReportBatchWorker",
        lambda **kwargs: created.setdefault("worker", _DependencyCapture(**kwargs)),
    )

    worker = batch_service.get_report_batch_worker()

    assert isinstance(worker, _DependencyCapture)
    assert worker.kwargs["batch_ledger"] is batch_ledger
    assert worker.kwargs["dispatcher"] is created["dispatcher"]
    assert worker.kwargs["execution_service"] is created["execution"]

    assert created["dispatcher"].kwargs == {
        "batch_ledger": batch_ledger,
        "report_job_ledger": job_ledger,
    }
    assert created["execution"].kwargs == {
        "batch_ledger": batch_ledger,
        "report_job_ledger": job_ledger,
        "capture_service": capture_service,
        "render_service": render_service,
    }


def test_get_report_batch_runtime_reuses_shared_components(monkeypatch) -> None:
    batch_service.get_report_batch_ledger.cache_clear()
    batch_ledger = _DependencyCapture(label="batch-ledger")
    job_ledger = _DependencyCapture(label="job-ledger")
    created = {}

    monkeypatch.setattr(batch_service, "get_report_batch_ledger", lambda: batch_ledger)
    monkeypatch.setattr(batch_service, "get_report_job_ledger", lambda: job_ledger)
    monkeypatch.setattr(
        batch_service,
        "get_portfolio_review_snapshot_capture_service",
        lambda: _DependencyCapture(label="capture-service"),
    )
    monkeypatch.setattr(
        batch_service,
        "get_portfolio_review_render_orchestration_service",
        lambda: _DependencyCapture(label="render-service"),
    )
    monkeypatch.setattr(
        batch_service,
        "ReportBatchDispatcher",
        lambda **kwargs: created.setdefault("dispatcher", _DependencyCapture(**kwargs)),
    )
    monkeypatch.setattr(
        batch_service,
        "ReportBatchExecutionService",
        lambda **kwargs: created.setdefault("execution", _DependencyCapture(**kwargs)),
    )
    monkeypatch.setattr(
        batch_service,
        "ReportBatchWorker",
        lambda **kwargs: created.setdefault("worker", _DependencyCapture(**kwargs)),
    )
    monkeypatch.setattr(
        batch_service,
        "ReportBatchRuntime",
        lambda **kwargs: created.setdefault("runtime", _DependencyCapture(**kwargs)),
    )

    runtime = batch_service.get_report_batch_runtime()

    assert isinstance(runtime, _DependencyCapture)
    assert runtime.kwargs["batch_ledger"] is batch_ledger
    assert runtime.kwargs["worker"] is created["worker"]
    assert created["worker"].kwargs["batch_ledger"] is batch_ledger
    assert created["worker"].kwargs["dispatcher"] is created["dispatcher"]
    assert created["worker"].kwargs["execution_service"] is created["execution"]


def test_get_report_batch_scheduler_constructs_configured_portfolio_source(monkeypatch) -> None:
    batch_service.get_report_batch_ledger.cache_clear()
    batch_ledger = _DependencyCapture(label="batch-ledger")
    captured = {}

    monkeypatch.setattr(
        batch_service,
        "settings",
        SimpleNamespace(
            report_job_ledger_database_url="postgres://rpt-batch.local/db",
            core_query_base_url="https://core-query.local",
            upstream_timeout_seconds=1.5,
            upstream_max_retries=3,
            upstream_retry_backoff_seconds=0.5,
        ),
    )
    monkeypatch.setattr(batch_service, "get_report_batch_ledger", lambda: batch_ledger)
    monkeypatch.setattr(
        batch_service,
        "CoreQueryClient",
        lambda **kwargs: captured.setdefault("portfolio_source", _DependencyCapture(**kwargs)),
    )
    monkeypatch.setattr(
        batch_service,
        "ReportBatchScheduler",
        lambda **kwargs: captured.setdefault("scheduler", _DependencyCapture(**kwargs)),
    )

    scheduler = batch_service.get_report_batch_scheduler()

    assert isinstance(scheduler, _DependencyCapture)
    assert scheduler.kwargs == {
        "batch_ledger": batch_ledger,
        "portfolio_source": captured["portfolio_source"],
    }
    assert captured["portfolio_source"].kwargs == {
        "base_url": "https://core-query.local",
        "timeout_seconds": 1.5,
        "max_retries": 3,
        "retry_backoff_seconds": 0.5,
    }
