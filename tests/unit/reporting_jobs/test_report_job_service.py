from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_jobs.service import get_report_job_worker
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy


def test_worker_service_composes_one_ledger_across_queue_and_execution(monkeypatch):
    ledger = object()
    capture_service = object()
    render_service = object()
    retry_policy = ReportJobWorkRetryPolicy(max_attempts=7)

    monkeypatch.setattr("app.reporting_jobs.service.get_report_job_ledger", lambda: ledger)
    monkeypatch.setattr(
        "app.reporting_lineage.service.get_portfolio_review_snapshot_capture_service",
        lambda: capture_service,
    )
    monkeypatch.setattr(
        "app.reporting_render.service.get_portfolio_review_render_orchestration_service",
        lambda: render_service,
    )

    worker = get_report_job_worker(retry_policy=retry_policy)

    assert worker._work_ledger is ledger
    assert worker._retry_policy is retry_policy
    assert isinstance(worker._execution_service, ReportJobExecutionService)
    assert worker._execution_service._report_job_ledger is ledger
    assert worker._execution_service._capture_service is capture_service
    assert worker._execution_service._render_service is render_service
