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
    OutcomeReviewReportJobRequest,
    PortfolioReviewJobRequest,
    ProofPackReportJobRequest,
    ReportCallerContext,
    ReportJobListFilters,
    WaveReportJobRequest,
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


def _memo_package(**overrides):
    payload = {
        "package_status": "INCLUDED_ADVISOR_PROPOSAL_MEMO",
        "usage": "REPORT_REQUEST_APPROVED_ADVISOR_MEMO",
        "memo_id": "memo_001",
        "memo_version": "advisory-proposal-memo-evidence-pack.v1",
        "memo_status": "READY",
        "proposal_id": "pp_001",
        "proposal_version_no": 1,
        "memo_hash": "sha256:" + "a" * 64,
        "source_input_hash": "sha256:" + "b" * 64,
        "review": {"review_action": "APPROVE_FOR_ADVISOR_USE"},
        "sections": [{"section_id": "EXECUTIVE_SUMMARY", "summary": "Advisor memo."}],
        "client_ready_publication": "BLOCKED",
    }
    payload.update(overrides)
    return payload


def test_portfolio_review_request_accepts_advisor_proposal_memo_package() -> None:
    request = _request(proposal_memo_package=_memo_package())

    assert request.proposal_memo_package is not None
    assert request.proposal_memo_package.memo_id == "memo_001"
    assert request.proposal_memo_package.review["review_action"] == "APPROVE_FOR_ADVISOR_USE"


def test_portfolio_review_request_rejects_client_ready_memo_package() -> None:
    with pytest.raises(ValueError):
        _request(proposal_memo_package=_memo_package(client_ready_publication="CLIENT_READY"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"package_status": "BLOCKED"},
            "proposal_memo_package.package_status must be INCLUDED_ADVISOR_PROPOSAL_MEMO",
        ),
        (
            {"review": {"review_action": "REQUEST_CHANGES"}},
            "proposal_memo_package.review.review_action must be APPROVE_FOR_ADVISOR_USE",
        ),
        (
            {"memo_hash": "memo-hash"},
            "proposal memo package hashes must use sha256 lineage",
        ),
        (
            {"sections": []},
            "proposal_memo_package.sections is required",
        ),
    ],
)
def test_portfolio_review_request_rejects_invalid_advisor_proposal_memo_package(
    overrides,
    message,
) -> None:
    with pytest.raises(ValueError, match=message):
        _request(proposal_memo_package=_memo_package(**overrides))


def test_portfolio_review_request_rejects_invalid_reviewed_narrative_package() -> None:
    package = {
        "package_status": "INCLUDED_REVIEWED_NARRATIVE",
        "usage": "REPORT_REQUEST_APPROVED_ADVISOR_NARRATIVE",
        "proposal_id": "prop_001",
        "proposal_version_no": 1,
        "narrative_id": "pnar_001",
        "narrative_status": "APPROVED_FOR_ADVISOR_USE",
        "audience": "advisor",
        "policy_version": "proposal-narrative-policy.v1",
        "review": {"review_state": "REQUEST_CHANGES"},
        "source_lineage": {"source_narrative_hash": "sha256:narrative"},
    }

    with pytest.raises(
        ValueError,
        match="proposal_narrative_package.review.review_state must be APPROVED_FOR_ADVISOR_USE",
    ):
        _request(proposal_narrative_package=package)

    package["review"] = {"review_state": "APPROVED_FOR_ADVISOR_USE"}
    package["source_lineage"] = {}
    with pytest.raises(
        ValueError,
        match="proposal_narrative_package.source_lineage.source_narrative_hash is required",
    ):
        _request(proposal_narrative_package=package)


def _outcome_request(**overrides):
    outcome_report_input = {
        "contract_version": "1.0",
        "outcome_review_id": "dor_001",
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "review_window": {"start_date": "2026-04-22", "end_date": "2026-04-23"},
        "generated_at": "2026-04-23T09:00:00Z",
        "content_hash": "sha256:report-input",
    }
    payload = {
        "outcome_report_input": outcome_report_input,
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }
    payload.update(overrides)
    return OutcomeReviewReportJobRequest.model_validate(payload)


def _proof_pack_request(**overrides):
    proof_pack_report_input = {
        "contract_version": "1.0",
        "proof_pack_id": "dpp_001",
        "proof_pack_content_hash": "sha256:proof-pack",
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "as_of_date": "2026-05-03",
        "generated_at": "2026-05-03T09:00:00Z",
        "content_hash": "sha256:report-input",
    }
    payload = {
        "proof_pack_report_input": proof_pack_report_input,
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }
    payload.update(overrides)
    return ProofPackReportJobRequest.model_validate(payload)


def _wave_request(**overrides):
    wave_report_input = {
        "contract_version": "1.0",
        "wave_id": "dwv_001",
        "wave_content_hash": "sha256:wave",
        "wave_state": "HANDOFF_READY",
        "trigger_type": "EXPLICIT_PORTFOLIO_LIST",
        "trigger_id": "manual-wave-001",
        "as_of_date": "2026-05-03",
        "generated_at": "2026-05-03T09:00:00Z",
        "items": [
            {
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "proof_pack_id": "dpp_001",
            },
            {
                "portfolio_id": "PB_SG_INCOME_002",
                "proof_pack_id": "dpp_002",
            },
        ],
        "content_hash": "sha256:wave-report-input",
    }
    payload = {
        "wave_report_input": wave_report_input,
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }
    payload.update(overrides)
    return WaveReportJobRequest.model_validate(payload)


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
    assert events[0].event_schema_version == "report-status-event.v1"
    assert events[0].event_family == "job_lifecycle"
    assert events[0].event_payload["report_type"] == "portfolio_review"
    assert events[0].event_idempotency_key == "idem-1"


def test_report_job_ledger_creates_outcome_review_request_job(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    record = ledger.create_outcome_review_report_job(
        request=_outcome_request(),
        caller_context=_caller(),
        idempotency_key="idem-outcome",
    )

    assert record.report_type == "outcome_review"
    assert record.status == "accepted"
    assert record.portfolio_scope == {
        "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
        "outcome_review_id": "dor_001",
    }
    assert record.as_of_date == date(2026, 4, 23)
    assert record.options["outcome_report_input"]["content_hash"] == "sha256:report-input"
    events = ledger.list_status_events(record.job_id)
    assert events[0].message == "Outcome review report job accepted."


def test_report_job_ledger_returns_duplicate_outcome_review_job(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    first = ledger.create_outcome_review_report_job(
        request=_outcome_request(),
        caller_context=_caller(),
        idempotency_key="idem-outcome-duplicate",
    )
    second = ledger.create_outcome_review_report_job(
        request=_outcome_request(),
        caller_context=_caller(),
        idempotency_key="idem-outcome-duplicate",
    )

    assert second.request_id == first.request_id
    assert second.job_id == first.job_id
    assert len(ledger.list_status_events(first.job_id)) == 1


def test_report_job_ledger_creates_proof_pack_request_job(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    record = ledger.create_proof_pack_report_job(
        request=_proof_pack_request(),
        caller_context=_caller(),
        idempotency_key="idem-proof-pack",
    )

    assert record.report_type == "proof_pack"
    assert record.status == "accepted"
    assert record.portfolio_scope == {
        "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
        "proof_pack_id": "dpp_001",
    }
    assert record.as_of_date == date(2026, 5, 3)
    assert record.options["proof_pack_report_input"]["content_hash"] == "sha256:report-input"
    events = ledger.list_status_events(record.job_id)
    assert events[0].message == "Proof-pack report job accepted."


def test_report_job_ledger_creates_wave_request_job(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    record = ledger.create_wave_report_job(
        request=_wave_request(),
        caller_context=_caller(),
        idempotency_key="idem-wave",
    )

    assert record.report_type == "rebalance_wave"
    assert record.status == "accepted"
    assert record.portfolio_scope == {
        "portfolio_ids": ["PB_SG_GLOBAL_BAL_001", "PB_SG_INCOME_002"],
        "wave_id": "dwv_001",
        "proof_pack_ids": ["dpp_001", "dpp_002"],
    }
    assert record.as_of_date == date(2026, 5, 3)
    assert record.options["wave_report_input"]["content_hash"] == "sha256:wave-report-input"
    events = ledger.list_status_events(record.job_id)
    assert events[0].message == "Rebalance wave report job accepted."


def test_report_job_ledger_validates_outcome_review_identity_and_window(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    missing_portfolio_request = _outcome_request(
        outcome_report_input={
            "contract_version": "1.0",
            "outcome_review_id": "dor_001",
            "review_window": {"end_date": "2026-04-23"},
        }
    )
    missing_window_request = _outcome_request(
        outcome_report_input={
            "contract_version": "1.0",
            "outcome_review_id": "dor_001",
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        }
    )

    with pytest.raises(ValueError, match="portfolio_id is required"):
        ledger.create_outcome_review_report_job(
            request=missing_portfolio_request,
            caller_context=_caller(),
            idempotency_key="idem-missing-portfolio",
        )
    with pytest.raises(ValueError, match="review window end date is required"):
        ledger.create_outcome_review_report_job(
            request=missing_window_request,
            caller_context=_caller(),
            idempotency_key="idem-missing-window",
        )


def test_report_job_ledger_validates_proof_pack_identity_and_as_of_date(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    missing_portfolio_request = _proof_pack_request(
        proof_pack_report_input={
            "proof_pack_id": "dpp_001",
            "as_of_date": "2026-05-03",
        }
    )
    missing_as_of_request = _proof_pack_request(
        proof_pack_report_input={
            "proof_pack_id": "dpp_001",
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        }
    )

    with pytest.raises(ValueError, match="portfolio_id is required"):
        ledger.create_proof_pack_report_job(
            request=missing_portfolio_request,
            caller_context=_caller(),
            idempotency_key="idem-proof-missing-portfolio",
        )
    with pytest.raises(ValueError, match="as_of_date is required"):
        ledger.create_proof_pack_report_job(
            request=missing_as_of_request,
            caller_context=_caller(),
            idempotency_key="idem-proof-missing-as-of",
        )


def test_report_job_ledger_validates_wave_identity_and_as_of_date(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    missing_wave_id_request = _wave_request(
        wave_report_input={
            "as_of_date": "2026-05-03",
            "items": [{"portfolio_id": "PB_SG_GLOBAL_BAL_001"}],
        }
    )
    missing_as_of_request = _wave_request(
        wave_report_input={
            "wave_id": "dwv_001",
            "items": [{"portfolio_id": "PB_SG_GLOBAL_BAL_001"}],
        }
    )

    with pytest.raises(ValueError, match="wave_report_input.wave_id is required"):
        ledger.create_wave_report_job(
            request=missing_wave_id_request,
            caller_context=_caller(),
            idempotency_key="idem-wave-missing-id",
        )
    with pytest.raises(ValueError, match="wave_report_input.as_of_date is required"):
        ledger.create_wave_report_job(
            request=missing_as_of_request,
            caller_context=_caller(),
            idempotency_key="idem-wave-missing-as-of",
        )


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


def test_report_job_ledger_rejects_proof_pack_idempotency_key_reuse(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    ledger.create_proof_pack_report_job(
        request=_proof_pack_request(),
        caller_context=_caller(),
        idempotency_key="idem-proof-conflict",
    )

    changed_request = _proof_pack_request(
        proof_pack_report_input={
            "proof_pack_id": "dpp_001",
            "proof_pack_content_hash": "sha256:changed-proof-pack",
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "as_of_date": "2026-05-03",
            "generated_at": "2026-05-03T09:00:00Z",
            "content_hash": "sha256:changed-report-input",
        }
    )

    with pytest.raises(IdempotencyConflictError):
        ledger.create_proof_pack_report_job(
            request=changed_request,
            caller_context=_caller(),
            idempotency_key="idem-proof-conflict",
        )


def test_report_job_ledger_requires_proof_pack_idempotency_key(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    with pytest.raises(MissingIdempotencyKeyError):
        ledger.create_proof_pack_report_job(
            request=_proof_pack_request(),
            caller_context=_caller(),
            idempotency_key=" ",
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
    assert events[-1].event_payload["current_step"] == "cancelled"


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
    failed_event = ledger.list_status_events(failed.job_id)[-1]
    assert failed_event.event_payload["failure_category"] == "validation_failed"
    assert failed_event.event_payload["failure_message"] == (
        "Requested report inputs were not fully supported."
    )


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
    events = ledger.list_status_events(ready.job_id)
    render_event = next(event for event in events if event.event_type == "job_rendering")
    assert render_event.event_family == "render_lifecycle"
    assert render_event.event_payload["render_job_id"] == f"rdr_{ready.job_id}_pdf"
    assert render_event.event_payload["render_template_id"] == "portfolio-review"
    archived_event = events[-1]
    assert archived_event.event_family == "archive_lifecycle"
    assert archived_event.event_payload["archive_document_id"] == "doc_123"


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
    assert event.event_schema_version == "report-status-event.legacy.v0"
    assert event.event_payload["payload_posture"] == "legacy_message_only"


def test_report_job_ledger_rejects_invalid_event_payload(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    record = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-invalid-event-payload",
    )

    with pytest.raises(ValueError, match="report_status_event_payload_missing:snapshot_id"):
        ledger.append_job_event(
            job_id=record.job_id,
            event_type="job_rerender_requested",
            message="Report rerender requested.",
            actor="advisor-123",
            correlation_id="corr-invalid-event",
            trace_id="trace-invalid-event",
        )

    with pytest.raises(ValueError, match="report_status_event_payload_sensitive_keys:portfolio_id"):
        ledger.append_job_event(
            job_id=record.job_id,
            event_type="job_rerender_requested",
            message="Report rerender requested.",
            event_payload={"snapshot_id": "rsnap_123", "portfolio_id": "PB_SG_GLOBAL_BAL_001"},
            actor="advisor-123",
            correlation_id="corr-sensitive-event",
            trace_id="trace-sensitive-event",
        )
