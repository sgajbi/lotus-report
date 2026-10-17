from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from io import BytesIO
from struct import pack
from typing import Any, Iterator, Mapping
from zoneinfo import ZoneInfo

import pytest

from app.reporting_jobs.custody_policy import REQUIRED_POLICY
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
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy


def _fixture_zone(key: str) -> ZoneInfo:
    """Small TZif fixtures: deterministic 2026 rules, independent of host tzdata."""
    if key == "America/New_York":
        transitions = [
            int(datetime(2026, 3, 8, 7, tzinfo=UTC).timestamp()),
            int(datetime(2026, 11, 1, 6, tzinfo=UTC).timestamp()),
        ]
        indices = bytes([1, 0])
        types = pack(">lBB", -18000, 0, 0) + pack(">lBB", -14400, 1, 4)
        names = b"EST\0EDT\0"
    else:
        offsets = {"UTC": 0, "Etc/UTC": 0, "Asia/Singapore": 28800}
        transitions, indices = [], b""
        types = pack(">lBB", offsets[key], 0, 0)
        names = b"FIX\0"
    header = (
        b"TZif\0" + bytes(15) + pack(">6l", 0, 0, 0, len(transitions), len(types) // 6, len(names))
    )
    data = header + b"".join(pack(">l", value) for value in transitions) + indices + types + names
    return ZoneInfo.from_file(BytesIO(data), key=key)


@pytest.mark.parametrize("fold", [0, 1])
@pytest.mark.parametrize("offset", [0, 8, -5])
def test_database_expiry_standalone_preserves_retry_instant(offset, fold):
    from app.reporting_jobs.postgres_ledger import _database_lease_expiry
    from app.reporting_jobs.work_queue import decide_report_job_work_failure

    instant = datetime(2026, 10, 12, 23, 59, tzinfo=UTC)
    fetched = instant.astimezone(timezone(timedelta(hours=offset))).replace(fold=fold)
    normalized = _database_lease_expiry(fetched)
    decision = decide_report_job_work_failure(
        attempt_count=2, failed_at=normalized, retry_policy=ReportJobWorkRetryPolicy()
    )
    assert normalized == instant and normalized.tzinfo is UTC
    assert normalized.fold == 0
    assert decision.available_at == instant + timedelta(seconds=10)
    assert decision.available_at.tzinfo is UTC


@pytest.mark.parametrize("invalid", ["naive", "tz_callback", "datetime_callback"])
def test_database_expiry_standalone_refuses_before_callbacks(invalid):
    from app.reporting_jobs.postgres_ledger import _database_lease_expiry

    class HostileZone(tzinfo):
        def utcoffset(self, value):
            raise AssertionError("untrusted timezone callback")

    class HostileDatetime(datetime):
        def astimezone(self, zone):
            raise AssertionError("untrusted datetime callback")

    values = {
        "naive": datetime(2026, 1, 1),
        "tz_callback": datetime(2026, 1, 1, tzinfo=HostileZone()),
        "datetime_callback": HostileDatetime(2026, 1, 1, tzinfo=UTC),
    }
    with pytest.raises(ValueError, match="report_job_work_lease_expiry_invalid"):
        _database_lease_expiry(values[invalid])


@pytest.mark.parametrize(
    "zone,instant,fold",
    [
        ("UTC", datetime(2026, 10, 12, 23, 59, tzinfo=UTC), 0),
        ("Etc/UTC", datetime(2026, 10, 12, 23, 59, tzinfo=UTC), 0),
        ("Asia/Singapore", datetime(2026, 10, 12, 23, 59, tzinfo=UTC), 0),
        ("America/New_York", datetime(2026, 11, 1, 5, 30, tzinfo=UTC), 0),
        ("America/New_York", datetime(2026, 11, 1, 6, 30, tzinfo=UTC), 1),
    ],
    ids=["utc", "named-utc", "singapore", "dst-first", "dst-second"],
)
def test_native_database_expiry_session_timezone(zone, instant, fold, record_property):
    """Opt-in scalar driver proof; no application rows or write transactions."""
    import os
    import platform
    import sys
    from importlib.metadata import version

    if os.environ.get("REPORT_NATIVE_QUALIFICATION") != "rpt446-native-20261014":
        pytest.skip("Delivery Lead native runtime admission required")
    import psycopg
    from psycopg import pq
    from psycopg._capabilities import capabilities

    from app.reporting_jobs.postgres_ledger import _database_lease_expiry
    from app.reporting_jobs.work_queue import decide_report_job_work_failure

    assert sys.platform == "linux" and platform.machine() == "x86_64"
    assert platform.python_implementation() == "CPython"
    assert psycopg.__version__ == "3.3.5" and pq.__impl__ == "binary"
    assert version("psycopg-binary") == "3.3.5"
    assert capabilities.has_cancel_safe()
    dsn = os.environ.get("REPORT_NATIVE_READONLY_DSN")
    assert dsn, "operator-supplied read-only connection required"
    record_property("python", sys.version)
    record_property("psycopg", psycopg.__version__)
    record_property("libpq_runtime", pq.version())
    record_property("libpq_build", pq.__build_version__)
    with psycopg.connect(
        dsn,
        connect_timeout=3,
        application_name="rpt446-native-20261014",
        options=f"-c default_transaction_read_only=on -c statement_timeout=2000 "
        f"-c lock_timeout=1000 -c timezone={zone}",
    ) as connection:
        row = connection.execute(
            "SELECT %s::timestamptz, current_setting('TimeZone'), "
            "current_setting('transaction_read_only'), current_setting('server_version_num')",
            (instant,),
        ).fetchone()
        assert row is not None and row[1] == zone and row[2] == "on"
        record_property("server_version_num", row[3])
        record_property("timezone", row[1])
        fetched = row[0]
        assert type(fetched) is datetime and fetched.fold == fold
        normalized = _database_lease_expiry(fetched)
        decision = decide_report_job_work_failure(
            attempt_count=2, failed_at=normalized, retry_policy=ReportJobWorkRetryPolicy()
        )
        assert normalized == instant and normalized.tzinfo is UTC
        assert decision.available_at == instant + timedelta(seconds=10)
        assert decision.available_at.tzinfo is UTC
        connection.rollback()


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
    assert event.event_schema_version == "report-status-event.legacy.v0"
    assert event.event_payload["payload_posture"] == "legacy_message_only"

    typed_event = _event_from_row(
        {
            "status_event_id": "rse_456",
            "report_job_id": "rjob_123",
            "from_status": "archiving",
            "to_status": "archived",
            "event_type": "job_archived",
            "event_schema_version": "report-status-event.v1",
            "event_family": "archive_lifecycle",
            "event_payload_json": {"archive_document_id": "doc_123"},
            "event_idempotency_key": "job_archived:rjob_123:doc_123",
            "message": "archived",
            "actor": "advisor-123",
            "created_at": datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
            "correlation_id": "corr-helpers",
            "trace_id": "trace-helpers",
        }
    )
    assert typed_event.event_family == "archive_lifecycle"
    assert typed_event.event_payload["archive_document_id"] == "doc_123"
    assert typed_event.event_idempotency_key == "job_archived:rjob_123:doc_123"


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


def test_postgres_report_job_ledger_lists_rerender_attempts_for_job() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _Connection([_rerender_row()])

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield connection

    ledger._connect = _connect  # type: ignore[method-assign]

    attempts = ledger.list_rerender_attempts("rjob_123", limit=10)

    assert [attempt.rerender_attempt_id for attempt in attempts] == ["rrnd_123"]
    assert connection.query is not None
    assert "FROM report_rerender_attempt" in connection.query
    assert "WHERE report_job_id = %s" in connection.query
    assert "ORDER BY updated_at DESC" in connection.query
    assert connection.params == ("rjob_123", 10)


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
                {"table_name": "report_job_work_item"},
            ],
            [{"table_name": "report_job_relationship"}],
            [
                {"column_name": "event_schema_version"},
                {"column_name": "event_family"},
                {"column_name": "event_payload_json"},
                {"column_name": "event_idempotency_key"},
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
                {"table_name": "report_job_work_item"},
            ],
            [{"table_name": "report_job_relationship"}],
            [
                {"column_name": "event_schema_version"},
                {"column_name": "event_family"},
                {"column_name": "event_payload_json"},
                {"column_name": "event_idempotency_key"},
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

    assert len(connection.calls) == 5


def test_postgres_report_job_ledger_append_job_event_records_current_status() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    connection = _ScriptedConnection([[{"status": "archived"}], []])
    _install_scripted_connect(ledger, connection)

    ledger.append_job_event(
        job_id="rjob_123",
        event_type="job_rerender_requested",
        message="Report rerender requested.",
        event_payload={"snapshot_id": "rsnap_123"},
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


def _work_clock_ledger(monkeypatch, policy):
    from types import SimpleNamespace

    local = datetime(2026, 10, 13, tzinfo=UTC)
    database = local + timedelta(hours=1)
    row = {
        "work_item_id": "work-clock",
        "report_job_id": "job-clock",
        "custody_policy": policy,
        "status": "pending",
        "attempt_count": 0,
        "available_at": local,
        "created_at": local,
        "updated_at": local,
        "lease_token": None,
        "lease_expires_at": None,
    }
    seen = {"clock": 0, "sweeps": 0, "claimed": 0}
    events = []
    seen["events"] = events

    def clock(connection, fallback):
        events.append("clock")
        seen["clock"] += 1
        return database

    def lock_pair(*args):
        events.append("lock")
        return row, {"custody_policy": policy}

    class Cursor:
        rowcount = 1

        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        def execute(self, query, values):
            if query.lstrip().startswith("SELECT"):
                expected = {"pending", "retry_pending"}
                if "WHERE work.status = 'leased'" in query or "WHERE status = 'leased'" in query:
                    expected = {"leased"}
                return Cursor([dict(row)] if row["status"] in expected else [])
            if "SET status = 'leased'" in query:
                row.update(
                    status="leased",
                    attempt_count=row["attempt_count"] + 1,
                    lease_owner=values[0],
                    lease_token=values[1],
                    lease_acquired_at=values[2],
                    lease_expires_at=values[3],
                    updated_at=values[4],
                )
            else:
                row.update(status=values[0], updated_at=values[2], lease_token=None)
            return Cursor([dict(row)])

    connection = Connection()

    @contextmanager
    def connect():
        yield connection

    def claimed(*args):
        seen["claimed"] += 1

    ledger = object.__new__(PostgresReportJobLedger)
    ledger.custody = SimpleNamespace(
        clock=clock,
        lock_pair=lock_pair,
        claimed=claimed,
    )
    monkeypatch.setattr(ledger, "_connect", connect)
    return ledger, connection, row, seen, local, database


def test_postgres_batch_does_not_recover_its_own_zero_duration_lease(monkeypatch):
    ledger, _, row, seen, local, _ = _work_clock_ledger(monkeypatch, None)

    def sweep(**kwargs):
        seen["sweeps"] += 1
        if row["status"] == "leased":
            row["status"] = "retry_pending"
        return 0, 0

    monkeypatch.setattr(ledger, "recover_expired_work_items", sweep)
    claimed = ledger.claim_work_items(worker_id="owner", limit=10, lease_seconds=0, now=local)
    assert len(claimed) == 1
    assert seen["sweeps"] == 1
    assert seen["claimed"] == 1
    assert row["status"] == "leased"
    assert claimed[0].lease_token == row["lease_token"]


@pytest.mark.parametrize("policy", [None, REQUIRED_POLICY])
def test_postgres_claim_preserves_legacy_clock_and_uses_post_lock_custody_clock(
    monkeypatch, policy
):
    ledger, _, row, seen, local, database = _work_clock_ledger(monkeypatch, policy)
    monkeypatch.setattr(ledger, "recover_expired_work_items", lambda **kw: (0, 0))
    claimed = ledger.claim_work_items(worker_id="owner", limit=1, lease_seconds=300, now=local)
    expected = local if policy is None else database
    assert claimed[0].lease_acquired_at == expected
    assert claimed[0].lease_expires_at == expected + timedelta(seconds=300)
    assert row["updated_at"] == expected
    assert seen["clock"] == (0 if policy is None else 1)
    assert seen["events"] == ([] if policy is None else ["lock", "clock"])


@pytest.mark.parametrize("policy", [None, REQUIRED_POLICY])
def test_postgres_recovery_preserves_legacy_clock_and_uses_post_lock_custody_clock(
    monkeypatch, policy
):
    ledger, connection, row, seen, local, database = _work_clock_ledger(monkeypatch, policy)
    row.update(
        status="leased",
        attempt_count=1,
        lease_owner="owner",
        lease_token="token",
        lease_expires_at=local - timedelta(seconds=1),
    )
    terminalized = []
    monkeypatch.setattr(
        ledger,
        "_terminalize_report_job_from_work",
        lambda **kw: terminalized.append(kw["work_item_id"]),
    )
    result = ledger._recover_expired_work_items(
        connection=connection,
        recovered_at=local,
        retry_policy=ReportJobWorkRetryPolicy(max_attempts=1),
    )
    assert result == (0, 1)
    assert row["status"] == "failed"
    assert row["updated_at"] == (local if policy is None else database)
    assert terminalized == ["work-clock"]
    assert seen["clock"] == (0 if policy is None else 1)
    assert seen["events"] == (["lock"] if policy is None else ["lock", "clock"])


@pytest.mark.parametrize("policy", [None, REQUIRED_POLICY])
@pytest.mark.parametrize(
    "zone,instant",
    [
        ("UTC", datetime(2026, 10, 12, 23, 59, tzinfo=UTC)),
        ("Etc/UTC", datetime(2026, 10, 12, 23, 59, tzinfo=UTC)),
        ("Asia/Singapore", datetime(2026, 10, 12, 23, 59, tzinfo=UTC)),
        ("America/New_York", datetime(2026, 11, 1, 5, 30, tzinfo=UTC)),
        ("America/New_York", datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
    ],
    ids=["utc", "named-utc", "singapore", "dst-first", "dst-second"],
)
def test_postgres_recovery_normalizes_expiry_before_retry_update(
    monkeypatch, policy, zone, instant
):
    ledger, connection, row, seen, _, _ = _work_clock_ledger(monkeypatch, policy)
    recovered_at = instant + timedelta(minutes=1)
    database_at = recovered_at + timedelta(seconds=1)
    monkeypatch.setattr(ledger.custody, "clock", lambda *_: database_at)
    row.update(
        status="leased",
        attempt_count=2,
        lease_owner="owner",
        lease_token="original-lease",
        lease_expires_at=instant.astimezone(_fixture_zone(zone)),
    )
    calls = []
    execute = connection.execute

    def capture(query, values):
        calls.append((query, values))
        return execute(query, values)

    monkeypatch.setattr(connection, "execute", capture)
    assert ledger._recover_expired_work_items(
        connection=connection,
        recovered_at=recovered_at,
        retry_policy=ReportJobWorkRetryPolicy(),
    ) == (1, 0)
    assert len(calls) == 2
    query, values = calls[1]
    effective_at = recovered_at if policy is None else database_at
    assert values == (
        "retry_pending",
        instant + timedelta(seconds=10),
        effective_at,
        "work-clock",
        "original-lease",
        effective_at,
    )
    assert values[1].tzinfo is UTC and values[1].fold == 0
    assert "AND lease_token = %s" in query and "AND lease_expires_at <= %s" in query
    assert row["status"] == "retry_pending" and row["lease_token"] is None
    assert seen["events"] == ["lock"]


@pytest.mark.parametrize(
    "invalid",
    ["naive", "nonexistent-local-time"],
    ids=["naive", "nonexistent-local-time"],
)
def test_postgres_recovery_refuses_invalid_expiry_before_lock_or_update(monkeypatch, invalid):
    ledger, connection, row, seen, local, _ = _work_clock_ledger(monkeypatch, REQUIRED_POLICY)
    expiry = (
        datetime(2026, 1, 1)
        if invalid == "naive"
        else datetime(2026, 3, 8, 2, 30, tzinfo=_fixture_zone("America/New_York"))
    )
    row.update(status="leased", lease_token="original-lease", lease_expires_at=expiry)
    with pytest.raises(ValueError, match="report_job_work_lease_expiry_invalid"):
        ledger._recover_expired_work_items(
            connection=connection,
            recovered_at=local,
            retry_policy=ReportJobWorkRetryPolicy(),
        )
    assert row["status"] == "leased" and row["lease_token"] == "original-lease"
    assert seen["events"] == []
