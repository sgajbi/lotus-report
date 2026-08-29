from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.report_batch_orchestrator.schedule_definitions import ScheduleDefinitionService

from app.clients.core_query_client import CoreQueryClient
from app.config import settings
from app.postgres import get_postgres_connection_provider
from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher
from app.report_batch_orchestrator.execution import ReportBatchExecutionService
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger
from app.report_batch_orchestrator.runtime import ReportBatchRuntime
from app.report_batch_orchestrator.scheduler import ReportBatchScheduler
from app.report_batch_orchestrator.worker import ReportBatchWorker
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_render.service import get_portfolio_review_render_orchestration_service


@lru_cache(maxsize=1)
def get_report_batch_ledger() -> PostgresReportBatchLedger:
    return PostgresReportBatchLedger(connection_provider=get_postgres_connection_provider())


def get_schedule_definition_service() -> "ScheduleDefinitionService":
    from app.report_batch_orchestrator.schedule_definitions import ScheduleDefinitionService

    return ScheduleDefinitionService(get_report_batch_ledger())


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


def get_report_batch_scheduler() -> ReportBatchScheduler:
    return ReportBatchScheduler(
        batch_ledger=get_report_batch_ledger(),
        portfolio_source=CoreQueryClient(
            base_url=settings.core_query_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        ),
    )
