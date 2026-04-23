from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, Iterator, Mapping

import pytest

from app.reporting_jobs.ledger import IdempotencyConflictError
from app.reporting_jobs.models import ReportJobListFilters
from app.reporting_jobs.postgres_ledger import (
    PostgresReportJobLedger,
    _event_from_row,
    _record_from_row,
)


class _Cursor:
    def __init__(self, rows: list[Mapping[str, Any]]):
        self._rows = rows

    def fetchall(self) -> list[Mapping[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[Mapping[str, Any]]):
        self.rows = rows
        self.query: str | None = None
        self.params: tuple[Any, ...] | None = None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.query = query
        self.params = params
        return _Cursor(self.rows)


def _row(*, job_id: str = "rjob_123", tenant_id: str = "tenant-sg") -> Mapping[str, Any]:
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    return {
        "report_request_id": "rrq_123",
        "report_job_id": job_id,
        "report_type": "portfolio_review",
        "request_portfolio_scope_json": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "requested_output_formats_json": ["json"],
        "as_of_date": date(2026, 4, 22),
        "reporting_currency": "USD",
        "options_json": {"sections": ["OVERVIEW"]},
        "trigger_type": "user",
        "triggered_by": "advisor-123",
        "caller_application": "lotus-gateway",
        "tenant_id": tenant_id,
        "region": "APAC",
        "booking_center_code": "SG",
        "role": "advisor",
        "idempotency_key": "idem-helpers",
        "request_hash": "hash-123",
        "correlation_id": "corr-helpers",
        "trace_id": "trace-helpers",
        "request_created_at": now,
        "status": "accepted",
        "failure_category": None,
        "failure_message": None,
        "current_step": "accepted",
        "retry_eligible": False,
        "cancel_requested": False,
        "job_created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "cancelled_at": None,
    }


def test_postgres_report_job_ledger_list_jobs_builds_expected_filters() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _Connection([_row()])

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield connection

    ledger._connect = _connect  # type: ignore[method-assign]

    records = ledger.list_jobs(
        filters=ReportJobListFilters(
            limit=25,
            tenant_id="tenant-sg",
            region="APAC",
            status="accepted",
            report_type="portfolio_review",
            portfolio_id="PB_SG_GLOBAL_BAL_001",
            as_of_date=date(2026, 4, 22),
            idempotency_key="idem-helpers",
            correlation_id="corr-helpers",
            created_from=datetime(2026, 4, 23, 11, 0, tzinfo=UTC),
            created_to=datetime(2026, 4, 23, 13, 0, tzinfo=UTC),
        )
    )

    assert [record.job_id for record in records] == ["rjob_123"]
    assert connection.query is not None
    assert "req.tenant_id = %s" in connection.query
    assert "req.region = %s" in connection.query
    assert "job.status = %s" in connection.query
    assert "req.report_type = %s" in connection.query
    assert "jsonb_array_elements_text" in connection.query
    assert "req.as_of_date = %s" in connection.query
    assert "req.idempotency_key = %s" in connection.query
    assert "req.correlation_id = %s" in connection.query
    assert "job.created_at >= %s" in connection.query
    assert "job.created_at <= %s" in connection.query
    assert connection.params is not None
    assert connection.params[-1] == 25


def test_postgres_report_job_ledger_existing_or_conflict_and_row_helpers() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    expected_record = _record_from_row(_row())

    def _load_by_request_id(_connection: object, request_id: str):
        assert request_id == "rrq_123"
        return expected_record

    ledger._load_by_request_id = _load_by_request_id  # type: ignore[method-assign]

    recovered = ledger._existing_or_conflict(
        object(),
        {"report_request_id": "rrq_123", "request_hash": "hash-123"},
        "hash-123",
    )
    assert recovered == expected_record

    with pytest.raises(
        IdempotencyConflictError,
        match="idempotency_key_reused_with_different_request",
    ):
        ledger._existing_or_conflict(
            object(),
            {"report_request_id": "rrq_123", "request_hash": "hash-123"},
            "other-hash",
        )

    event = _event_from_row(
        {
            "status_event_id": "rse_123",
            "report_job_id": "rjob_123",
            "from_status": None,
            "to_status": "accepted",
            "event_type": "job_accepted",
            "message": "accepted",
            "actor": "advisor-123",
            "created_at": datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            "correlation_id": "corr-helpers",
            "trace_id": "trace-helpers",
        }
    )
    assert event.to_status == "accepted"
    assert event.created_at == datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
