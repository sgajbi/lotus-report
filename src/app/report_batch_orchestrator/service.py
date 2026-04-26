from functools import lru_cache

from app.config import settings
from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher
from app.report_batch_orchestrator.execution import ReportBatchExecutionService
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger
from app.report_batch_orchestrator.runtime import ReportBatchRuntime
from app.report_batch_orchestrator.worker import ReportBatchWorker
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_render.service import get_portfolio_review_render_orchestration_service


@lru_cache(maxsize=1)
def get_report_batch_ledger() -> PostgresReportBatchLedger:
    return PostgresReportBatchLedger(settings.report_job_ledger_database_url)


def get_report_batch_worker() -> ReportBatchWorker:
    batch_ledger = get_report_batch_ledger()
    report_job_ledger = get_report_job_ledger()
    return ReportBatchWorker(
        batch_ledger=batch_ledger,
        dispatcher=ReportBatchDispatcher(
            batch_ledger=batch_ledger,
            report_job_ledger=report_job_ledger,
        ),
        execution_service=ReportBatchExecutionService(
            batch_ledger=batch_ledger,
            report_job_ledger=report_job_ledger,
            capture_service=get_portfolio_review_snapshot_capture_service(),
            render_service=get_portfolio_review_render_orchestration_service(),
        ),
    )


def get_report_batch_runtime() -> ReportBatchRuntime:
    batch_ledger = get_report_batch_ledger()
    report_job_ledger = get_report_job_ledger()
    return ReportBatchRuntime(
        batch_ledger=batch_ledger,
        worker=ReportBatchWorker(
            batch_ledger=batch_ledger,
            dispatcher=ReportBatchDispatcher(
                batch_ledger=batch_ledger,
                report_job_ledger=report_job_ledger,
            ),
            execution_service=ReportBatchExecutionService(
                batch_ledger=batch_ledger,
                report_job_ledger=report_job_ledger,
                capture_service=get_portfolio_review_snapshot_capture_service(),
                render_service=get_portfolio_review_render_orchestration_service(),
            ),
        ),
    )
