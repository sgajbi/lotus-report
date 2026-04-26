from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.report_batch_orchestrator.ledger import BatchIdempotencyConflictError
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    PortfolioBatchCandidate,
)
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger
from app.reporting_jobs.models import ReportCallerContext


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL batch ledger proof")
    return database_url


def _caller(unique_suffix: str) -> ReportCallerContext:
    return ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id=f"corr-pg-batch-{unique_suffix}",
        trace_id=f"trace-pg-batch-{unique_suffix}",
    )


def _request(unique_suffix: str, portfolio_id: str) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=[portfolio_id],
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id=portfolio_id,
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
            )
        ],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW"], "proof": unique_suffix},
    )


def test_postgres_batch_ledger_persists_idempotent_materialized_batch() -> None:
    unique_suffix = uuid4().hex
    ledger = PostgresReportBatchLedger(_database_url())
    ledger.check_ready()
    request = _request(unique_suffix, f"PB_SG_GLOBAL_BAL_001_{unique_suffix}")
    caller = _caller(unique_suffix)

    first = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key=f"batch-pg-{unique_suffix}",
    )
    second = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key=f"batch-pg-{unique_suffix}",
    )

    assert second == first
    assert first.item_count == 1
    assert first.items[0].portfolio_id == f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"

    with pytest.raises(BatchIdempotencyConflictError):
        ledger.create_batch(
            request=_request(unique_suffix, f"PB_SG_GLOBAL_BAL_002_{unique_suffix}"),
            caller_context=caller,
            idempotency_key=f"batch-pg-{unique_suffix}",
        )
