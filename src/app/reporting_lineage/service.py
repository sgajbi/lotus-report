from functools import lru_cache

from app.postgres import get_postgres_connection_provider
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.capture_service import (
    PortfolioReviewSnapshotCaptureService,
    ReportingReadPortfolioReviewInputProvider,
)
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore


@lru_cache
def get_report_input_snapshot_store() -> PostgresReportInputSnapshotStore:
    return PostgresReportInputSnapshotStore(connection_provider=get_postgres_connection_provider())


@lru_cache(maxsize=1)
def get_portfolio_review_snapshot_capture_service() -> PortfolioReviewSnapshotCaptureService:
    return PortfolioReviewSnapshotCaptureService(
        snapshot_store=get_report_input_snapshot_store(),
        job_ledger=get_report_job_ledger(),
        portfolio_review_input_provider=ReportingReadPortfolioReviewInputProvider(),
    )
