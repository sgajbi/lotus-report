from functools import lru_cache

from app.postgres import get_postgres_connection_provider
from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
from app.reporting_jobs.worker import ReportJobWorker


@lru_cache(maxsize=1)
def get_report_job_ledger() -> PostgresReportJobLedger:
    return PostgresReportJobLedger(connection_provider=get_postgres_connection_provider())


def get_report_job_worker(
    *, retry_policy: ReportJobWorkRetryPolicy | None = None
) -> ReportJobWorker:
    from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
    from app.reporting_render.service import get_portfolio_review_render_orchestration_service

    ledger = get_report_job_ledger()
    return ReportJobWorker(
        work_ledger=ledger,
        execution_service=ReportJobExecutionService(
            report_job_ledger=ledger,
            capture_service=get_portfolio_review_snapshot_capture_service(),
            render_service=get_portfolio_review_render_orchestration_service(),
        ),
        retry_policy=retry_policy,
    )
