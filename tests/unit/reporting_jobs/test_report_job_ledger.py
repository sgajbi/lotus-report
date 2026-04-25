from datetime import UTC, date, datetime

import pytest

from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
    ReportJobNotFoundError,
    _dt_from_text,
    _dt_to_text,
    _event_from_row,
    _record_from_row,
    _record_matches_filters,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobListFilters,
)


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


def test_report_job_ledger_lists_and_filters_jobs(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    accepted = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(correlation_id="corr-accepted"),
        idempotency_key="idem-list-accepted",
    )
    cancelled = ledger.create_portfolio_review_job(
        request=_request(
            portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_002"]},
            as_of_date="2026-04-23",
        ),
        caller_context=_caller(
            tenant_id="tenant-hk",
            region="HKG",
            correlation_id="corr-cancelled",
        ),
        idempotency_key="idem-list-cancelled",
    )
    ledger.cancel_job(
        job_id=cancelled.job_id,
        actor="advisor-123",
        correlation_id="corr-cancelled-transition",
        trace_id="trace-cancelled-transition",
    )

    all_records = ledger.list_jobs(filters=ReportJobListFilters(limit=10))
    assert [record.job_id for record in all_records] == [cancelled.job_id, accepted.job_id]

    accepted_only = ledger.list_jobs(
        filters=ReportJobListFilters(
            limit=10,
            tenant_id="tenant-sg",
            region="APAC",
            status="accepted",
            report_type="portfolio_review",
            portfolio_id="PB_SG_GLOBAL_BAL_001",
            as_of_date=date(2026, 4, 22),
            idempotency_key="idem-list-accepted",
            correlation_id="corr-accepted",
            created_from=accepted.created_at,
            created_to=accepted.updated_at,
        )
    )
    assert [record.job_id for record in accepted_only] == [accepted.job_id]

    no_match = ledger.list_jobs(filters=ReportJobListFilters(limit=10, tenant_id="tenant-nowhere"))
    assert no_match == []


def test_report_job_ledger_marks_collecting_data_data_ready_and_failed(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    ready = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(correlation_id="corr-ready"),
        idempotency_key="idem-ready",
    )
    failed = ledger.create_portfolio_review_job(
        request=_request(portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_002"]}),
        caller_context=_caller(correlation_id="corr-failed", trace_id="trace-failed"),
        idempotency_key="idem-failed",
    )

    collecting = ledger.mark_collecting_data(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id="corr-ready-step",
        trace_id="trace-ready-step",
    )
    assert collecting.status == "collecting_data"
    assert collecting.started_at is not None

    data_ready = ledger.mark_data_ready(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id="corr-ready-finish",
        trace_id="trace-ready-finish",
    )
    assert data_ready.status == "data_ready"
    assert data_ready.current_step == "data_ready"
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "collecting_data",
        "data_ready",
    ]

    failed_record = ledger.mark_failed(
        job_id=failed.job_id,
        actor="advisor-123",
        correlation_id="corr-failed-step",
        trace_id="trace-failed-step",
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


def test_report_job_ledger_marks_rendering_and_completed(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(requested_output_formats=["pdf"]),
        caller_context=_caller(correlation_id="corr-render"),
        idempotency_key="idem-rendering",
    )
    ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id="corr-render-ready",
        trace_id="trace-render-ready",
    )

    rendering = ledger.mark_rendering(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id="corr-rendering",
        trace_id="trace-rendering",
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
    )
    assert rendering.status == "rendering"
    assert rendering.render_job_id == f"rdr_{ready.job_id}_pdf"

    completed = ledger.mark_completed(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id="corr-render-complete",
        trace_id="trace-render-complete",
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
        artifact_sha256="sha256:artifact",
        bounded_determinism_fingerprint="fingerprint",
        runtime_engine="typst",
        runtime_engine_version="0.14.2",
        render_duration_ms=812,
    )
    assert completed.status == "completed"
    assert completed.render_artifact_sha256 == "sha256:artifact"
    assert completed.completed_at is not None

    archiving = ledger.mark_archiving(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id="corr-archive-start",
        trace_id="trace-archive-start",
        archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
    )
    assert archiving.status == "archiving"
    assert archiving.archive_request_id == f"arch_rdr_{ready.job_id}_pdf"

    archived = ledger.mark_archived(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id="corr-archive-complete",
        trace_id="trace-archive-complete",
        archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
        archive_document_id="doc_123",
    )
    assert archived.status == "archived"
    assert archived.archive_document_id == "doc_123"
    assert archived.archive_completed_at is not None
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
        "archiving",
        "archived",
    ]


def test_report_job_ledger_transition_helper_handles_not_found_same_status_and_invalid_path(
    tmp_path,
):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-transition-branches",
    )

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.mark_collecting_data(
            job_id="rjob_missing",
            actor="advisor-123",
            correlation_id="corr-missing-transition",
            trace_id="trace-missing-transition",
        )

    data_ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id="corr-first-ready",
        trace_id="trace-first-ready",
    )
    same_status = ledger.mark_data_ready(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id="corr-second-ready",
        trace_id="trace-second-ready",
    )
    assert same_status == data_ready

    with pytest.raises(
        InvalidReportJobTransitionError,
        match="report_job_invalid_transition",
    ):
        ledger.mark_collecting_data(
            job_id=job.job_id,
            actor="advisor-123",
            correlation_id="corr-invalid-transition",
            trace_id="trace-invalid-transition",
        )


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (ReportJobListFilters(limit=10, tenant_id="tenant-other"), False),
        (ReportJobListFilters(limit=10, region="EMEA"), False),
        (ReportJobListFilters(limit=10, status="failed"), False),
        (ReportJobListFilters(limit=10, report_type="other"), False),
        (ReportJobListFilters(limit=10, portfolio_id="PB_OTHER"), False),
        (ReportJobListFilters(limit=10, as_of_date=date(2026, 4, 23)), False),
        (ReportJobListFilters(limit=10, idempotency_key="other"), False),
        (ReportJobListFilters(limit=10, correlation_id="other"), False),
        (
            ReportJobListFilters(
                limit=10,
                created_from=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
            ),
            False,
        ),
        (
            ReportJobListFilters(
                limit=10,
                created_to=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
            ),
            False,
        ),
        (ReportJobListFilters(limit=10), True),
    ],
)
def test_report_job_ledger_filter_helper_covers_all_branch_paths(filters, expected) -> None:
    record = _record_from_row(
        {
            "report_request_id": "rrq_123",
            "report_job_id": "rjob_123",
            "report_type": "portfolio_review",
            "request_portfolio_scope_json": '{"portfolio_ids":["PB_SG_GLOBAL_BAL_001"]}',
            "requested_output_formats_json": '["json"]',
            "as_of_date": "2026-04-22",
            "reporting_currency": "USD",
            "options_json": '{"sections":["OVERVIEW"]}',
            "trigger_type": "user",
            "triggered_by": "advisor-123",
            "caller_application": "lotus-gateway",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "booking_center_code": "SG",
            "role": "advisor",
            "idempotency_key": "idem-helpers",
            "request_hash": "hash-123",
            "status": "accepted",
            "failure_category": None,
            "failure_message": None,
            "current_step": "accepted",
            "retry_eligible": 0,
            "cancel_requested": 0,
            "job_created_at": "2026-04-23T12:00:00Z",
            "updated_at": "2026-04-23T12:00:00Z",
            "started_at": None,
            "completed_at": None,
            "cancelled_at": None,
            "correlation_id": "corr-helpers",
            "trace_id": "trace-helpers",
        }
    )

    assert _record_matches_filters(record, filters) is expected


def test_report_job_ledger_helpers_round_trip_rows() -> None:
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    now_text = _dt_to_text(now)
    row = {
        "report_request_id": "rrq_123",
        "report_job_id": "rjob_123",
        "report_type": "portfolio_review",
        "request_portfolio_scope_json": '{"portfolio_ids":["PB_SG_GLOBAL_BAL_001"]}',
        "requested_output_formats_json": '["json"]',
        "as_of_date": "2026-04-22",
        "reporting_currency": "USD",
        "options_json": '{"sections":["OVERVIEW"]}',
        "trigger_type": "user",
        "triggered_by": "advisor-123",
        "caller_application": "lotus-gateway",
        "tenant_id": "tenant-sg",
        "region": "APAC",
        "booking_center_code": "SG",
        "role": "advisor",
        "idempotency_key": "idem-helpers",
        "request_hash": "hash-123",
        "status": "accepted",
        "failure_category": None,
        "failure_message": None,
        "current_step": "accepted",
        "retry_eligible": 0,
        "cancel_requested": 0,
        "job_created_at": now_text,
        "updated_at": now_text,
        "started_at": None,
        "completed_at": None,
        "cancelled_at": None,
        "correlation_id": "corr-helpers",
        "trace_id": "trace-helpers",
    }

    record = _record_from_row(row)
    assert record.job_id == "rjob_123"
    assert record.portfolio_scope == {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}
    assert record.requested_output_formats == ["json"]
    assert record.created_at == now
    assert _dt_from_text(None) is None
    assert _dt_from_text(now_text) == now

    event = _event_from_row(
        {
            "status_event_id": "rse_123",
            "report_job_id": "rjob_123",
            "from_status": None,
            "to_status": "accepted",
            "event_type": "job_accepted",
            "message": "accepted",
            "actor": "advisor-123",
            "created_at": now_text,
            "correlation_id": "corr-helpers",
            "trace_id": "trace-helpers",
        }
    )
    assert event.created_at == now
    assert event.to_status == "accepted"
