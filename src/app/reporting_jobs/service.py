from functools import lru_cache

from app.postgres import get_postgres_connection_provider
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger


@lru_cache(maxsize=1)
def get_report_job_ledger() -> PostgresReportJobLedger:
    return PostgresReportJobLedger(connection_provider=get_postgres_connection_provider())
