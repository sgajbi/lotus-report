from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, Iterator, Mapping
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from app.config import settings
from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
)
from app.reporting_jobs.postgres_ledger import (
    PostgresReportJobLedger,
    _date_from_value,
    _dt_from_value,
)
from app.reporting_jobs.service import get_report_job_ledger


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL ledger proof")
    return database_url


def _request_and_context(
    unique_suffix: str,
) -> tuple[PortfolioReviewJobRequest, ReportCallerContext]:
    request = PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": [f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"]},
        as_of_date="2026-04-22",
        requested_output_formats=["json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW"], "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40"},
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
    return request, caller_context


def _ledger() -> PostgresReportJobLedger:
    return PostgresReportJobLedger(_database_url())


def test_postgres_report_job_ledger_persists_idempotent_job_and_status_events() -> None:
    ledger = _ledger()
    ledger.check_ready()

    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)

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


def test_postgres_report_job_ledger_marks_collecting_data_data_ready_and_failed() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)

    ready = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-ready-{unique_suffix}",
    )
    failed = ledger.create_portfolio_review_job(
        request=request.model_copy(
            update={"portfolio_scope": {"portfolio_ids": [f"PB_SG_GLOBAL_BAL_002_{unique_suffix}"]}}
        ),
        caller_context=caller_context.model_copy(
            update={
                "correlation_id": f"corr-pg-failed-{unique_suffix}",
                "trace_id": f"trace-pg-failed-{unique_suffix}",
            }
        ),
        idempotency_key=f"portfolio-review-pg-failed-{unique_suffix}",
    )

    collecting = ledger.mark_collecting_data(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-collect-{unique_suffix}",
        trace_id=f"trace-pg-collect-{unique_suffix}",
    )
    assert collecting.status == "collecting_data"
    assert collecting.started_at is not None

    data_ready = ledger.mark_data_ready(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-data-ready-{unique_suffix}",
        trace_id=f"trace-pg-data-ready-{unique_suffix}",
    )
    assert data_ready.status == "data_ready"
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "collecting_data",
        "data_ready",
    ]

    failed_record = ledger.mark_failed(
        job_id=failed.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-mark-failed-{unique_suffix}",
        trace_id=f"trace-pg-mark-failed-{unique_suffix}",
        failure_category="validation_failed",
        failure_message="Requested report inputs were not fully supported.",
        retry_eligible=False,
    )
    assert failed_record.status == "failed"
    assert failed_record.failure_category == "validation_failed"
    assert failed_record.retry_eligible is False
    assert [event.to_status for event in ledger.list_status_events(failed.job_id)] == [
        "accepted",
        "failed",
    ]


def test_postgres_report_job_ledger_transition_helper_branches() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-transition-{unique_suffix}",
    )

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.mark_collecting_data(
            job_id=f"rjob_missing_{unique_suffix}",
            actor="advisor-123",
            correlation_id=f"corr-pg-missing-{unique_suffix}",
            trace_id=f"trace-pg-missing-{unique_suffix}",
        )

    data_ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-first-ready-{unique_suffix}",
        trace_id=f"trace-pg-first-ready-{unique_suffix}",
    )
    same_status = ledger.mark_data_ready(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-second-ready-{unique_suffix}",
        trace_id=f"trace-pg-second-ready-{unique_suffix}",
    )
    assert same_status == data_ready

    with pytest.raises(
        InvalidReportJobTransitionError,
        match="report_job_invalid_transition",
    ):
        ledger.mark_collecting_data(
            job_id=job.job_id,
            actor="advisor-123",
            correlation_id=f"corr-pg-invalid-{unique_suffix}",
            trace_id=f"trace-pg-invalid-{unique_suffix}",
        )


def test_postgres_report_job_ledger_rejects_missing_idempotency_key() -> None:
    request, caller_context = _request_and_context(uuid4().hex)

    with pytest.raises(MissingIdempotencyKeyError, match="missing_idempotency_key"):
        _ledger().create_portfolio_review_job(
            request=request,
            caller_context=caller_context,
            idempotency_key=" ",
        )


def test_postgres_report_job_ledger_rejects_idempotency_key_reuse_for_different_request() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    idempotency_key = f"portfolio-review-pg-conflict-{unique_suffix}"

    ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=idempotency_key,
    )

    conflicting_request = request.model_copy(
        update={"portfolio_scope": {"portfolio_ids": [f"PB_SG_ALT_BAL_001_{unique_suffix}"]}}
    )

    with pytest.raises(
        IdempotencyConflictError,
        match="idempotency_key_reused_with_different_request",
    ):
        ledger.create_portfolio_review_job(
            request=conflicting_request,
            caller_context=caller_context,
            idempotency_key=idempotency_key,
        )


def test_postgres_report_job_ledger_handles_missing_and_terminal_job_transitions() -> None:
    ledger = _ledger()
    unknown_job_id = f"rjob_missing_{uuid4().hex}"

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.get_job(unknown_job_id)

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.cancel_job(
            job_id=unknown_job_id,
            actor="advisor-123",
            correlation_id="corr-missing-job",
            trace_id="trace-missing-job",
        )

    assert ledger.list_status_events(unknown_job_id) == []

    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-terminal-{unique_suffix}",
    )
    ledger.cancel_job(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id="corr-terminal-job",
        trace_id="trace-terminal-job",
    )

    with pytest.raises(
        InvalidReportJobTransitionError,
        match="report_job_cannot_be_cancelled",
    ):
        ledger.cancel_job(
            job_id=job.job_id,
            actor="advisor-123",
            correlation_id="corr-terminal-job-2",
            trace_id="trace-terminal-job-2",
        )


def test_postgres_report_job_ledger_check_ready_reports_missing_schema() -> None:
    ledger = object.__new__(PostgresReportJobLedger)

    class _Cursor:
        def fetchall(self) -> list[Mapping[str, Any]]:
            return [{"table_name": "report_request"}]

    class _Connection:
        def execute(self, *_args: object, **_kwargs: object) -> _Cursor:
            return _Cursor()

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield _Connection()

    ledger._connect = _connect  # type: ignore[method-assign]

    with pytest.raises(
        RuntimeError,
        match="report_job_ledger_schema_missing:report_job,report_status_event",
    ):
        ledger.check_ready()


def test_postgres_report_job_ledger_load_by_request_id_requires_existing_row() -> None:
    ledger = _ledger()

    with ledger._connect() as connection:
        with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
            ledger._load_by_request_id(connection, f"rrq_missing_{uuid4().hex}")


def test_postgres_report_job_ledger_rolls_back_failed_connection_scope() -> None:
    ledger = _ledger()

    with pytest.raises(RuntimeError, match="force rollback path"):
        with ledger._connect() as _connection:
            raise RuntimeError("force rollback path")


def test_postgres_report_job_ledger_translates_unique_violation_race_without_existing_row() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    request, caller_context = _request_and_context(uuid4().hex)

    class _InitialConnection:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise UniqueViolation("duplicate idempotency key")

    class _MissingExistingCursor:
        def fetchone(self) -> None:
            return None

    class _RecoveryConnection:
        def execute(self, *_args: object, **_kwargs: object) -> _MissingExistingCursor:
            return _MissingExistingCursor()

    connections: list[object] = [_InitialConnection(), _RecoveryConnection()]

    @contextmanager
    def _connect() -> Iterator[object]:
        yield connections.pop(0)

    ledger._connect = _connect  # type: ignore[method-assign]

    with pytest.raises(IdempotencyConflictError, match="idempotency_key_unique_violation"):
        ledger.create_portfolio_review_job(
            request=request,
            caller_context=caller_context,
            idempotency_key=f"portfolio-review-pg-race-{uuid4().hex}",
        )


def test_postgres_report_job_ledger_recovers_unique_violation_when_existing_row_is_visible() -> (
    None
):
    ledger = object.__new__(PostgresReportJobLedger)
    request, caller_context = _request_and_context(uuid4().hex)
    existing = {
        "report_request_id": "rrq_existing_visible",
        "request_hash": "existing-hash",
    }

    class _InitialConnection:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise UniqueViolation("duplicate idempotency key")

    class _ExistingCursor:
        def fetchone(self) -> Mapping[str, str]:
            return existing

    class _RecoveryConnection:
        def execute(self, *_args: object, **_kwargs: object) -> _ExistingCursor:
            return _ExistingCursor()

    connections: list[object] = [_InitialConnection(), _RecoveryConnection()]

    @contextmanager
    def _connect() -> Iterator[object]:
        yield connections.pop(0)

    def _existing_or_conflict(
        _connection: object,
        visible_existing: Mapping[str, Any],
        _request_hash: str,
    ) -> str:
        assert visible_existing == existing
        return "recovered-from-visible-existing-row"

    ledger._connect = _connect  # type: ignore[method-assign]
    ledger._existing_or_conflict = _existing_or_conflict  # type: ignore[method-assign]

    recovered = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-visible-race-{uuid4().hex}",
    )

    assert recovered == "recovered-from-visible-existing-row"


def test_postgres_report_job_ledger_cancel_detects_job_deleted_after_lock() -> None:
    ledger = object.__new__(PostgresReportJobLedger)

    class _StatusCursor:
        def fetchone(self) -> Mapping[str, str]:
            return {"status": "accepted"}

    class _MissingRowCursor:
        def fetchone(self) -> None:
            return None

    class _Connection:
        def __init__(self) -> None:
            self.statement_count = 0

        def execute(self, *_args: object, **_kwargs: object) -> _StatusCursor | _MissingRowCursor:
            self.statement_count += 1
            if self.statement_count == 1:
                return _StatusCursor()
            return _MissingRowCursor()

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield _Connection()

    ledger._connect = _connect  # type: ignore[method-assign]

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.cancel_job(
            job_id=f"rjob_deleted_after_lock_{uuid4().hex}",
            actor="advisor-123",
            correlation_id="corr-deleted-after-lock",
            trace_id="trace-deleted-after-lock",
        )


def test_postgres_report_job_ledger_value_parsers_accept_driver_and_text_values() -> None:
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)

    assert _dt_from_value(None) is None
    assert _dt_from_value(now) == now
    assert _dt_from_value("2026-04-23T12:00:00Z") == now
    assert _date_from_value(date(2026, 4, 23)) == date(2026, 4, 23)
    assert _date_from_value("2026-04-23") == date(2026, 4, 23)


def test_report_job_ledger_service_returns_postgres_ledger() -> None:
    settings.report_job_ledger_database_url = _database_url()
    get_report_job_ledger.cache_clear()
    try:
        ledger = get_report_job_ledger()
        ledger.check_ready()
        assert isinstance(ledger, PostgresReportJobLedger)
    finally:
        get_report_job_ledger.cache_clear()
