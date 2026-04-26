from functools import lru_cache

from app.config import settings
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger


@lru_cache(maxsize=1)
def get_report_batch_ledger() -> PostgresReportBatchLedger:
    return PostgresReportBatchLedger(settings.report_job_ledger_database_url)
