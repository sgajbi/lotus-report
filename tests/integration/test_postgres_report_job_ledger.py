from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL ledger proof")
    return database_url


def test_postgres_report_job_ledger_persists_idempotent_job_and_status_events() -> None:
    ledger = PostgresReportJobLedger(_database_url())
    unique_suffix = uuid4().hex
    request = PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": [f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"]},
        as_of_date="2026-04-22",
        requested_output_formats=["json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW"], "benchmark_code": "BMK_GLOBAL_BALANCED_60_40"},
    )
    caller_context = ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id=f"corr-pg-ledger-{unique_suffix}",
        trace_id=f"trace-pg-ledger-{unique_suffix}",
    )

    first = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-{unique_suffix}",
    )
    second = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-{unique_suffix}",
    )

    assert second == first
    assert ledger.get_job(first.job_id).job_id == first.job_id

    cancelled = ledger.cancel_job(
        job_id=first.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-cancel-{unique_suffix}",
        trace_id=f"trace-pg-cancel-{unique_suffix}",
    )

    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested is True
    assert [event.to_status for event in ledger.list_status_events(first.job_id)] == [
        "accepted",
        "cancelled",
    ]
