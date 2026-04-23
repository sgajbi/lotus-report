from functools import lru_cache

from app.config import settings
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger


@lru_cache(maxsize=1)
def get_report_job_ledger() -> PostgresReportJobLedger:
    return PostgresReportJobLedger(settings.report_job_ledger_database_url)
