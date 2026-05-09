from datetime import UTC, date, datetime

from app.reporting_jobs.models import (
    ReportJobLedgerRecord,
    ReportStatusEvent,
)
from app.reporting_jobs.portfolio_memory_events import build_report_portfolio_memory_events
from app.reporting_lineage.models import ReportInputSnapshotRecord


def _record(*, status="archived") -> ReportJobLedgerRecord:
    return ReportJobLedgerRecord(
        request_id="rrq_001",
        job_id="rjob_001",
        report_type="proof_pack",
        portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"], "proof_pack_id": "dpp_001"},
        requested_output_formats=["pdf"],
        as_of_date=date(2026, 5, 3),
        reporting_currency="USD",
        options={},
        trigger_type="user",
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        idempotency_key="proof-pack-rjob-001",
        request_hash="9b2f3551c2f8636e7d9c827ed48ce8c174d03e0cc67a9fd14cdb4f2bc91a7cfd",
        status=status,
        failure_category=None,
        failure_message=None,
        current_step=status,
        retry_eligible=False,
        cancel_requested=False,
        created_at=datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 3, 9, 4, tzinfo=UTC),
        started_at=datetime(2026, 5, 3, 9, 0, tzinfo=UTC),
        completed_at=datetime(2026, 5, 3, 9, 3, tzinfo=UTC),
        cancelled_at=None,
        correlation_id="corr-001",
        trace_id="trace-001",
        render_job_id="rdr_rjob_001_pdf",
        render_output_format="pdf",
        render_template_id="proof-pack",
        render_template_version="v1",
        render_artifact_sha256="sha256:artifact-proof-pack",
        render_bounded_determinism_fingerprint="typst-0.14.2:proof",
        render_runtime_engine="typst",
        render_runtime_engine_version="0.14.2",
        render_duration_ms=812,
        archive_request_id="arch_rjob_001_pdf",
        archive_document_id="doc_rjob_001",
        archive_completed_at=datetime(2026, 5, 3, 9, 4, tzinfo=UTC),
    )


def _record_without_portfolio() -> ReportJobLedgerRecord:
    record = _record()
    return record.model_copy(update={"portfolio_scope": {"wave_id": "dwv_001"}})


def _event(event_type: str, to_status: str, minute: int) -> ReportStatusEvent:
    return ReportStatusEvent(
        status_event_id=f"rse_{event_type}",
        report_job_id="rjob_001",
        from_status=None,
        to_status=to_status,
        event_type=event_type,
        message=None,
        actor="advisor-123",
        created_at=datetime(2026, 5, 3, 9, minute, tzinfo=UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
    )


def _snapshot() -> ReportInputSnapshotRecord:
    return ReportInputSnapshotRecord(
        snapshot_id="rsnap_001",
        report_job_id="rjob_001",
        report_type="proof_pack",
        report_data_contract_version="dpm_proof_pack_report_input.v1",
        portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        as_of_date=date(2026, 5, 3),
        snapshot_payload={"proof_pack_id": "dpp_001"},
        snapshot_hash="sha256:snapshot-proof-pack",
        snapshot_storage_ref=None,
        supportability_status="complete",
        completeness_status="complete",
        lineage_summary={"source_services": ["lotus-manage"], "supportability_status": "complete"},
        captured_at=datetime(2026, 5, 3, 9, 1, tzinfo=UTC),
        created_at=datetime(2026, 5, 3, 9, 1, tzinfo=UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
    )


def test_report_portfolio_memory_events_are_support_safe_and_artifact_backed():
    response = build_report_portfolio_memory_events(
        record=_record(),
        status_events=[
            _event("job_accepted", "accepted", 0),
            _event("job_data_ready", "data_ready", 1),
            _event("job_archived", "archived", 4),
        ],
        snapshot=_snapshot(),
        generated_at=datetime(2026, 5, 3, 9, 5, tzinfo=UTC),
    )

    assert response.report_job_id == "rjob_001"
    assert response.portfolio_id == "PB_SG_GLOBAL_BAL_001"
    assert response.supportability_state == "READY"
    assert response.event_count == 3
    assert response.governance_policy.redaction_policy == "NO_RAW_PAYLOADS"
    archived = response.events[-1]
    assert archived.event_type == "REPORT_ARCHIVED"
    assert archived.event_identity.startswith("lotus-report:REPORT_STATUS_EVENT:rse_job_archived:")
    assert {ref.source_type for ref in archived.source_refs} == {
        "REPORT_JOB",
        "REPORT_STATUS_EVENT",
        "REPORT_INPUT_SNAPSHOT",
    }
    assert [artifact.artifact_system for artifact in archived.artifact_refs] == [
        "lotus-render",
        "lotus-archive",
    ]
    assert "snapshot_payload" not in response.model_dump_json().lower()
    assert "snapshot_storage_ref" not in response.model_dump_json().lower()


def test_report_portfolio_memory_events_degrade_failed_jobs_without_snapshot():
    response = build_report_portfolio_memory_events(
        record=_record(status="failed"),
        status_events=[
            _event("job_accepted", "accepted", 0),
            _event("job_failed", "failed", 2),
        ],
        snapshot=None,
        generated_at=datetime(2026, 5, 3, 9, 5, tzinfo=UTC),
    )

    assert response.supportability_state == "DEGRADED"
    assert response.reason_codes == ["REPORT_EVENT_FAMILY_DEGRADED"]
    assert response.events[-1].event_type == "REPORT_JOB_FAILED"
    assert response.events[-1].supportability_state == "DEGRADED"
    assert all(
        "REPORT_INPUT_SNAPSHOT" not in {ref.source_type for ref in event.source_refs}
        for event in response.events
    )


def test_report_portfolio_memory_events_report_empty_family_without_portfolio_scope():
    response = build_report_portfolio_memory_events(
        record=_record_without_portfolio(),
        status_events=[],
        snapshot=None,
        generated_at=datetime(2026, 5, 3, 9, 5, tzinfo=UTC),
    )

    assert response.portfolio_id is None
    assert response.supportability_state == "EMPTY"
    assert response.reason_codes == ["REPORT_EVENT_FAMILY_EMPTY"]
    assert response.events == []


def test_report_portfolio_memory_events_report_pending_family_before_ready_state():
    response = build_report_portfolio_memory_events(
        record=_record(status="collecting_data"),
        status_events=[
            _event("job_accepted", "accepted", 0),
            _event("job_collecting_data", "collecting_data", 1),
        ],
        snapshot=None,
        generated_at=datetime(2026, 5, 3, 9, 5, tzinfo=UTC),
    )

    assert response.supportability_state == "PENDING_REVIEW"
    assert response.reason_codes == ["REPORT_EVENT_FAMILY_PENDING_REVIEW"]
    assert [event.supportability_state for event in response.events] == [
        "PENDING_REVIEW",
        "PENDING_REVIEW",
    ]
