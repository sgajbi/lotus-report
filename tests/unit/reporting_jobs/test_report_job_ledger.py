import pytest

from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext


def _request(**overrides):
    payload = {
        "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "as_of_date": "2026-04-22",
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
    }
    payload.update(overrides)
    return PortfolioReviewJobRequest.model_validate(payload)


def _caller(**overrides):
    payload = {
        "triggered_by": "advisor-123",
        "caller_application": "lotus-gateway",
        "tenant_id": "tenant-sg",
        "region": "APAC",
        "booking_center_code": "SG",
        "role": "advisor",
        "correlation_id": "corr-100",
        "trace_id": "trace-100",
    }
    payload.update(overrides)
    return ReportCallerContext.model_validate(payload)


def test_report_job_ledger_creates_request_job_and_append_only_event(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    record = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-1",
    )

    assert record.request_id.startswith("rrq_")
    assert record.job_id.startswith("rjob_")
    assert record.report_type == "portfolio_review"
    assert record.status == "accepted"
    assert record.current_step == "accepted"
    assert record.portfolio_scope == {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}
    assert record.requested_output_formats == ["json"]
    assert record.retry_eligible is False
    assert record.cancel_requested is False
    assert record.correlation_id == "corr-100"
    assert record.trace_id == "trace-100"

    events = ledger.list_status_events(record.job_id)
    assert len(events) == 1
    assert events[0].from_status is None
    assert events[0].to_status == "accepted"
    assert events[0].event_type == "job_accepted"


def test_report_job_ledger_returns_duplicate_for_same_idempotency_key_and_hash(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    first = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-duplicate",
    )
    second = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-duplicate",
    )

    assert second.request_id == first.request_id
    assert second.job_id == first.job_id
    assert len(ledger.list_status_events(first.job_id)) == 1


def test_report_job_ledger_rejects_idempotency_key_reuse_with_different_request(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-conflict",
    )

    with pytest.raises(IdempotencyConflictError):
        ledger.create_portfolio_review_job(
            request=_request(reporting_currency="CHF"),
            caller_context=_caller(),
            idempotency_key="idem-conflict",
        )


def test_report_job_ledger_requires_idempotency_key(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    with pytest.raises(MissingIdempotencyKeyError):
        ledger.create_portfolio_review_job(
            request=_request(),
            caller_context=_caller(),
            idempotency_key=None,
        )


def test_report_job_ledger_cancels_pre_render_job_and_records_transition(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    record = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-cancel",
    )

    cancelled = ledger.cancel_job(
        job_id=record.job_id,
        actor="advisor-123",
        correlation_id="corr-101",
        trace_id="trace-101",
    )

    assert cancelled.status == "cancelled"
    assert cancelled.failure_category == "cancelled"
    assert cancelled.cancel_requested is True
    assert cancelled.cancelled_at is not None
    events = ledger.list_status_events(record.job_id)
    assert [event.to_status for event in events] == ["accepted", "cancelled"]
    assert events[-1].from_status == "accepted"
    assert events[-1].event_type == "job_cancelled"


def test_report_job_ledger_rejects_duplicate_cancel(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    record = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-cancel-repeat",
    )
    ledger.cancel_job(
        job_id=record.job_id,
        actor="advisor-123",
        correlation_id="corr-101",
        trace_id="trace-101",
    )

    with pytest.raises(InvalidReportJobTransitionError):
        ledger.cancel_job(
            job_id=record.job_id,
            actor="advisor-123",
            correlation_id="corr-102",
            trace_id="trace-102",
        )


def test_report_job_ledger_rejects_unknown_cancel_and_missing_request_load(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    with pytest.raises(ReportJobNotFoundError):
        ledger.cancel_job(
            job_id="rjob_missing",
            actor="advisor-123",
            correlation_id="corr-missing",
            trace_id="trace-missing",
        )

    with ledger._connect() as connection:
        with pytest.raises(ReportJobNotFoundError):
            ledger._load_by_request_id(connection, "rrq_missing")
