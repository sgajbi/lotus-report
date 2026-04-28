from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, Iterator, Mapping

import pytest

from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    MissingIdempotencyKeyError,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import ReportJobListFilters
from app.reporting_jobs.postgres_ledger import (
    PostgresReportJobLedger,
    _event_from_row,
    _record_from_row,
    _rerender_attempt_from_row,
)


class _Cursor:
    def __init__(self, rows: list[Mapping[str, Any]]):
        self._rows = rows

    def fetchone(self) -> Mapping[str, Any] | None:
        return self._rows[0] if self._rows else None

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


class _ScriptedConnection:
    def __init__(self, responses: list[list[Mapping[str, Any]]]):
        self._responses = responses
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.calls.append((query, params))
        rows = self._responses.pop(0) if self._responses else []
        return _Cursor(rows)


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


def _archived_job_row() -> Mapping[str, Any]:
    row = dict(_row())
    row.update(
        {
            "status": "archived",
            "current_step": "archived",
            "requested_output_formats_json": ["pdf"],
            "render_job_id": "rdr_original_pdf",
            "render_output_format": "pdf",
            "render_template_id": "portfolio-review",
            "render_template_version": "v1",
            "render_artifact_sha256": "sha256:original",
            "render_bounded_determinism_fingerprint": "fingerprint-original",
            "render_runtime_engine": "typst",
            "render_runtime_engine_version": "0.14.2",
            "render_duration_ms": 812,
            "archive_request_id": "arch_rdr_original_pdf",
            "archive_document_id": "doc_original_pdf",
            "archive_completed_at": datetime(2026, 4, 23, 12, 15, tzinfo=UTC),
        }
    )
    return row


def _rerender_row(
    *,
    status: str = "archived",
    retry_eligible: bool = False,
) -> Mapping[str, Any]:
    now = datetime(2026, 4, 23, 12, 30, tzinfo=UTC)
    return {
        "rerender_attempt_id": "rrnd_123",
        "report_job_id": "rjob_123",
        "idempotency_key": "rerender-rjob-123",
        "status": status,
        "snapshot_id": "rsnap_123",
        "snapshot_hash": "sha256:snapshot",
        "previous_render_job_id": "rdr_original_pdf",
        "previous_archive_document_id": "doc_original_pdf",
        "render_job_id": "rdr_rrnd_123_pdf",
        "render_output_format": "pdf",
        "render_template_id": "portfolio-review",
        "render_template_version": "v1",
        "render_artifact_sha256": "sha256:artifact",
        "render_bounded_determinism_fingerprint": "fingerprint",
        "render_runtime_engine": "typst",
        "render_runtime_engine_version": "0.14.2",
        "render_duration_ms": 731,
        "archive_request_id": "arch_rdr_rrnd_123_pdf",
        "archive_document_id": "doc_correction_pdf",
        "archive_completed_at": now,
        "failure_category": None,
        "failure_message": None,
        "retry_eligible": retry_eligible,
        "requested_by": "advisor-123",
        "reason": "Template correction.",
        "correlation_id": "corr-rerender",
        "trace_id": "trace-rerender",
        "created_at": now,
        "updated_at": now,
    }


def _install_scripted_connect(
    ledger: PostgresReportJobLedger,
    connection: _ScriptedConnection,
) -> None:
    @contextmanager
    def _connect() -> Iterator[_ScriptedConnection]:
        yield connection

    ledger._connect = _connect  # type: ignore[method-assign]


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


def test_postgres_report_job_ledger_list_jobs_uses_limit_only_when_filters_are_empty() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _Connection([_row(job_id="rjob_limit_only")])

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield connection

    ledger._connect = _connect  # type: ignore[method-assign]

    records = ledger.list_jobs(filters=ReportJobListFilters(limit=5))

    assert [record.job_id for record in records] == ["rjob_limit_only"]
    assert connection.query is not None
    assert "req.tenant_id = %s" not in connection.query
    assert "req.region = %s" not in connection.query
    assert "job.status = %s" not in connection.query
    assert connection.params == (5,)


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


def test_postgres_report_job_ledger_rerender_row_helper_maps_operational_fields() -> None:
    attempt = _rerender_attempt_from_row(_rerender_row())

    assert attempt.rerender_attempt_id == "rrnd_123"
    assert attempt.status == "archived"
    assert attempt.snapshot_hash == "sha256:snapshot"
    assert attempt.previous_render_job_id == "rdr_original_pdf"
    assert attempt.previous_archive_document_id == "doc_original_pdf"
    assert attempt.render_job_id == "rdr_rrnd_123_pdf"
    assert attempt.archive_document_id == "doc_correction_pdf"
    assert attempt.archive_completed_at == datetime(2026, 4, 23, 12, 30, tzinfo=UTC)
    assert attempt.retry_eligible is False


def test_postgres_report_job_ledger_rerender_update_raises_for_unknown_attempt() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _Connection([])

    with pytest.raises(ReportJobNotFoundError, match="report_rerender_attempt_not_found"):
        ledger._update_rerender_attempt(
            connection=connection,
            rerender_attempt_id="rrnd_missing",
            status="failed",
        )


def test_postgres_report_job_ledger_check_ready_requires_rerender_table() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection(
        [
            [
                {"table_name": "report_request"},
                {"table_name": "report_job"},
                {"table_name": "report_status_event"},
            ],
            [
                {"column_name": "archive_request_id"},
                {"column_name": "archive_document_id"},
                {"column_name": "archive_completed_at"},
            ],
            [],
        ]
    )
    _install_scripted_connect(ledger, connection)

    with pytest.raises(RuntimeError, match="report_rerender_attempt_schema_missing"):
        ledger.check_ready()


def test_postgres_report_job_ledger_check_ready_accepts_complete_schema() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection(
        [
            [
                {"table_name": "report_request"},
                {"table_name": "report_job"},
                {"table_name": "report_status_event"},
            ],
            [
                {"column_name": "archive_request_id"},
                {"column_name": "archive_document_id"},
                {"column_name": "archive_completed_at"},
            ],
            [{"table_name": "report_rerender_attempt"}],
        ]
    )
    _install_scripted_connect(ledger, connection)

    ledger.check_ready()

    assert len(connection.calls) == 3


def test_postgres_report_job_ledger_append_job_event_records_current_status() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection([[{"status": "archived"}], []])
    _install_scripted_connect(ledger, connection)

    ledger.append_job_event(
        job_id="rjob_123",
        event_type="job_rerender_requested",
        message="Report rerender requested.",
        actor="advisor-123",
        correlation_id="corr-rerender",
        trace_id="trace-rerender",
    )

    assert len(connection.calls) == 2
    assert connection.calls[1][1][2:5] == ("archived", "archived", "job_rerender_requested")


def test_postgres_report_job_ledger_append_job_event_rejects_unknown_job() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection([[]])
    _install_scripted_connect(ledger, connection)

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.append_job_event(
            job_id="rjob_missing",
            event_type="job_rerender_requested",
            message="Report rerender requested.",
            actor="advisor-123",
            correlation_id="corr-rerender",
            trace_id="trace-rerender",
        )


def test_postgres_report_job_ledger_create_rerender_attempt_inserts_audit_event() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection([[], [], [], [_rerender_row(status="rendering")]])
    _install_scripted_connect(ledger, connection)
    job = _record_from_row(_archived_job_row())

    attempt, created = ledger.create_rerender_attempt(
        job=job,
        snapshot_id="rsnap_123",
        snapshot_hash="sha256:snapshot",
        idempotency_key="  rerender-rjob-123  ",
        actor="advisor-123",
        reason="Template correction.",
        correlation_id="corr-rerender",
        trace_id="trace-rerender",
    )

    assert created is True
    assert attempt.status == "rendering"
    assert attempt.snapshot_id == "rsnap_123"
    assert connection.calls[1][1][2] == "rerender-rjob-123"
    assert connection.calls[2][1][4] == "job_rerender_requested"


def test_postgres_report_job_ledger_create_rerender_attempt_returns_existing() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection([[_rerender_row(status="archived")]])
    _install_scripted_connect(ledger, connection)
    job = _record_from_row(_archived_job_row())

    attempt, created = ledger.create_rerender_attempt(
        job=job,
        snapshot_id="rsnap_123",
        snapshot_hash="sha256:snapshot",
        idempotency_key="rerender-rjob-123",
        actor="advisor-123",
        reason="Template correction.",
        correlation_id="corr-rerender",
        trace_id="trace-rerender",
    )

    assert created is False
    assert attempt.status == "archived"
    assert len(connection.calls) == 1


def test_postgres_report_job_ledger_create_rerender_attempt_requires_key() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    job = _record_from_row(_archived_job_row())

    with pytest.raises(MissingIdempotencyKeyError, match="missing_idempotency_key"):
        ledger.create_rerender_attempt(
            job=job,
            snapshot_id="rsnap_123",
            snapshot_hash="sha256:snapshot",
            idempotency_key=" ",
            actor="advisor-123",
            reason="Template correction.",
            correlation_id="corr-rerender",
            trace_id="trace-rerender",
        )


def test_postgres_report_job_ledger_marks_rerender_rendered_and_archiving() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection(
        [
            [_rerender_row(status="rendering")],
            [],
            [_rerender_row(status="rendered")],
            [_rerender_row(status="rendered")],
            [],
            [_rerender_row(status="archiving")],
        ]
    )
    _install_scripted_connect(ledger, connection)

    rendered = ledger.mark_rerender_rendered(
        rerender_attempt_id="rrnd_123",
        render_job_id="rdr_rrnd_123_pdf",
        artifact_sha256="sha256:artifact",
        bounded_determinism_fingerprint="fingerprint",
        runtime_engine="typst",
        runtime_engine_version="0.14.2",
        render_duration_ms=731,
    )
    archiving = ledger.mark_rerender_archiving(
        rerender_attempt_id="rrnd_123",
        archive_request_id="arch_rdr_rrnd_123_pdf",
    )

    assert rendered.status == "rendered"
    assert archiving.status == "archiving"
    assert connection.calls[1][1][0] == "rendered"
    assert connection.calls[4][1][0] == "archiving"


def test_postgres_report_job_ledger_marks_rerender_archived_and_failed_events() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection(
        [
            [_rerender_row(status="archiving")],
            [],
            [_rerender_row(status="archived")],
            [],
            [_rerender_row(status="rendering")],
            [],
            [_rerender_row(status="failed", retry_eligible=True)],
            [],
        ]
    )
    _install_scripted_connect(ledger, connection)

    archived = ledger.mark_rerender_archived(
        rerender_attempt_id="rrnd_123",
        actor="advisor-123",
        correlation_id="corr-rerender",
        trace_id="trace-rerender",
        archive_document_id="doc_correction_pdf",
    )
    failed = ledger.mark_rerender_failed(
        rerender_attempt_id="rrnd_123",
        actor="advisor-123",
        correlation_id="corr-rerender",
        trace_id="trace-rerender",
        failure_category="render_execution_failed",
        failure_message="Render worker unavailable.",
        retry_eligible=True,
    )

    assert archived.status == "archived"
    assert failed.status == "failed"
    assert failed.retry_eligible is True
    assert connection.calls[3][1][4] == "job_rerender_archived"
    assert connection.calls[7][1][4] == "job_rerender_failed"
