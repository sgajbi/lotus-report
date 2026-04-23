from functools import lru_cache

from app.config import settings
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore


@lru_cache
def get_report_input_snapshot_store() -> PostgresReportInputSnapshotStore:
    return PostgresReportInputSnapshotStore(settings.report_job_ledger_database_url)
