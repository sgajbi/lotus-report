from functools import lru_cache

from app.config import settings
from app.reporting_jobs.ledger import ReportJobLedger


@lru_cache(maxsize=1)
def get_report_job_ledger() -> ReportJobLedger:
    return ReportJobLedger(settings.report_job_ledger_db_path)
