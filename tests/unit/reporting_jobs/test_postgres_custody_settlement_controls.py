"""Controlled PostgreSQL adapter outcomes; no native concurrency claim."""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.reporting_jobs.ledger import InvalidReportJobWorkTransitionError, ReportJobNotFoundError
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger


@pytest.fixture
def adapter(monkeypatch):
    now = datetime.now(UTC)
    work = {
        "work_item_id": "work",
        "report_job_id": "job",
        "status": "completed",
        "attempt_count": 1,
        "available_at": now,
        "created_at": now,
        "updated_at": now,
        "lease_token": None,
    }
    execute = Mock(return_value=SimpleNamespace(fetchone=lambda: work))
    connection = SimpleNamespace(execute=execute)
    ledger = object.__new__(PostgresReportJobLedger)
    ledger.custody = SimpleNamespace(
        validate_work=Mock(),
        protect_work=Mock(return_value=True),
        settle_verified_work=Mock(return_value=work),
        guard_transition=Mock(return_value=True),
    )

    @contextmanager
    def connect():
        yield connection

    monkeypatch.setattr(ledger, "_connect", connect)
    return ledger, connection, work


@pytest.mark.parametrize("operation", ["complete", "defer", "fail"])
def test_verified_settlement_returns_completed_work_without_retry_mutation(adapter, operation):
    ledger, connection, _ = adapter
    args = {"work_item_id": "work", "lease_token": "token"}
    if operation == "complete":
        result = ledger.complete_work_item(**args)
    elif operation == "defer":
        result = ledger.defer_work_item(**args, wait_reason="late", delay_seconds=1)
    else:
        result = ledger.fail_work_item(**args, error_category="late", error_summary="late")
    assert result.status == "completed"
    assert all(
        not call.args[0].lstrip().startswith("UPDATE") for call in connection.execute.call_args_list
    )


def test_protected_work_disappearing_after_settlement_refuses(adapter):
    ledger, connection, _ = adapter
    connection.execute.return_value = SimpleNamespace(fetchone=lambda: None)
    with pytest.raises(InvalidReportJobWorkTransitionError, match="work_item_not_found"):
        ledger.complete_work_item(work_item_id="work", lease_token="token")


@pytest.mark.parametrize("missing", [False, True])
def test_custody_blocked_cancellation_returns_current_job_or_not_found(
    adapter, monkeypatch, missing
):
    ledger, connection, _ = adapter
    job = object()
    load = Mock(return_value=job)
    monkeypatch.setattr(ledger, "_load_by_request_id", load)
    connection.execute.return_value = SimpleNamespace(
        fetchone=lambda: None if missing else {"report_request_id": "request"}
    )
    args = {"job_id": "job", "actor": "operator", "correlation_id": "corr", "trace_id": "trace"}
    if missing:
        with pytest.raises(ReportJobNotFoundError):
            ledger.cancel_job(**args)
        load.assert_not_called()
    else:
        assert ledger.cancel_job(**args) is job
        load.assert_called_once_with(connection, "request")


def test_terminalization_does_not_override_protected_custody(adapter):
    ledger, connection, _ = adapter
    ledger._terminalize_report_job_from_work(
        connection=connection,
        work_item_id="work",
        actor="worker",
        failure_category="timeout",
        failure_summary="late",
    )
    connection.execute.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_custody_guarded_transition_preserves_current_job_or_missing_contract(
    adapter, monkeypatch, missing
):
    ledger, connection, _ = adapter
    job = object()
    monkeypatch.setattr(ledger, "_load_by_request_id", Mock(return_value=job))
    connection.execute.return_value = SimpleNamespace(
        fetchone=lambda: None if missing else {"report_request_id": "request"}
    )
    args = {
        "connection": connection,
        "job_id": "job",
        "to_status": "failed",
        "failure_category": "timeout",
        "failure_message": "late",
        "current_step": "failed",
        "retry_eligible": False,
        "actor": "worker",
        "correlation_id": "corr",
        "trace_id": "trace",
        "event_type": "job_failed",
        "event_message": "late",
        "set_started_at": False,
        "set_completed_at": True,
        "render_job_id": None,
        "render_output_format": None,
        "render_template_id": None,
        "render_template_version": None,
        "render_artifact_sha256": None,
        "render_bounded_determinism_fingerprint": None,
        "render_runtime_engine": None,
        "render_runtime_engine_version": None,
        "render_duration_ms": None,
        "archive_request_id": None,
        "archive_document_id": None,
        "archive_completed_at": None,
    }
    if missing:
        with pytest.raises(ReportJobNotFoundError):
            ledger._transition_job(**args)
    else:
        assert ledger._transition_job(**args) is job


def test_unknown_work_ids_short_circuit_without_database_queries(adapter):
    ledger, connection, _ = adapter
    assert ledger.get_archive_statuses_by_job_ids([], tenant_id="tenant") == []
    connection.execute.assert_not_called()
