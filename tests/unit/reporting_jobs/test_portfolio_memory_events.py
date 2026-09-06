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
        event_schema_version="report-status-event.v1",
        event_family="job_lifecycle",
        event_payload={
            "event_type": event_type,
            "from_status": None,
            "to_status": to_status,
        },
        event_idempotency_key=None,
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


def _accepted_identity(*, record, snapshot) -> tuple[str, str]:
    """The identity a downstream consumer deduplicates on, for the ONE
    finished historical event every reading below shares."""

    response = build_report_portfolio_memory_events(
        record=record,
        status_events=[_event("job_accepted", "accepted", 0)],
        snapshot=snapshot,
    )
    accepted = response.events[0]
    return accepted.event_identity, accepted.content_hash


def test_lifecycle_progress_never_restates_a_finished_events_identity():
    """report#283: the same accepted event read at four lifecycle points.
    Under the v1 preimage this produced three different identities within
    one deployment, because the job's CURRENT snapshot, artifact and
    archive-document facts were hashed into every historical event."""

    readings = {
        "accepted": _accepted_identity(record=_record(status="accepted"), snapshot=None),
        "captured": _accepted_identity(record=_record(status="data_ready"), snapshot=_snapshot()),
        "rendered": _accepted_identity(record=_record(status="completed"), snapshot=_snapshot()),
        "archived": _accepted_identity(record=_record(status="archived"), snapshot=_snapshot()),
    }

    assert len(set(readings.values())) == 1, readings


def test_a_deployment_that_enriches_later_facts_leaves_history_untouched():
    """The deployment-borne half: a snapshot that gains a revision binding
    (and a job that gains custody identifiers) between two reads must not
    move the identity of an event that finished before either existed."""

    before = _accepted_identity(record=_record(status="accepted"), snapshot=None)
    enriched_snapshot = _snapshot().model_copy(
        update={
            "report_revision_id": "rrv3_enriched",
            "factual_content_digest": "sha256:facts-enriched",
            "snapshot_hash": "sha256:snapshot-restated",
        }
    )
    enriched_record = _record(status="archived").model_copy(
        update={
            "archive_document_id": "doc_enriched",
            "render_artifact_sha256": "sha256:artifact-enriched",
        }
    )
    after = _accepted_identity(record=enriched_record, snapshot=enriched_snapshot)

    assert before == after


def test_a_replayed_job_carries_its_own_event_identities():
    """Replay clones the snapshot verbatim, so the SAME captured facts sit
    under two jobs. Their events must still be distinct events - identity
    is per event, and the replayed job's history is its own."""

    source = _accepted_identity(record=_record(status="archived"), snapshot=_snapshot())
    replayed_record = _record(status="archived").model_copy(update={"job_id": "rjob_replayed"})
    replayed = _accepted_identity(record=replayed_record, snapshot=_snapshot())

    assert source != replayed


def test_downstream_deduplication_sees_each_historical_event_exactly_once():
    """The consequence the reproduction exists to protect: a consumer
    keyed on event_identity ingests the job's history once, however many
    times it reads during the lifecycle."""

    seen: dict[str, str] = {}
    for record, snapshot in (
        (_record(status="accepted"), None),
        (_record(status="data_ready"), _snapshot()),
        (_record(status="archived"), _snapshot()),
    ):
        response = build_report_portfolio_memory_events(
            record=record,
            status_events=[
                _event("job_accepted", "accepted", 0),
                _event("job_data_ready", "data_ready", 1),
            ],
            snapshot=snapshot,
        )
        for event in response.events:
            seen.setdefault(event.event_identity, event.event_id)

    assert len(seen) == 2, seen
    assert sorted(seen.values()) == [
        "report-memory:rjob_001:rse_job_accepted",
        "report-memory:rjob_001:rse_job_data_ready",
    ]


def test_an_event_cites_only_what_existed_when_it_happened():
    """Body and identity tell the same story: an accepted event cites no
    snapshot (none existed), while the data-ready event cites both the
    snapshot and - outside the identity preimage - its revision."""

    response = build_report_portfolio_memory_events(
        record=_record(status="data_ready"),
        status_events=[
            _event("job_accepted", "accepted", 0),
            _event("job_data_ready", "data_ready", 1),
        ],
        snapshot=_snapshot().model_copy(
            update={
                "report_revision_id": "rrv3_capture",
                "factual_content_digest": "sha256:facts-capture",
            }
        ),
    )
    accepted, data_ready = response.events

    assert [ref.source_type for ref in accepted.source_refs] == [
        "REPORT_JOB",
        "REPORT_STATUS_EVENT",
    ]
    revision_refs = [ref for ref in data_ready.source_refs if ref.source_type == "REPORT_REVISION"]
    assert [ref.source_id for ref in revision_refs] == ["rrv3_capture"]
    assert [ref.content_hash for ref in revision_refs] == ["sha256:facts-capture"]


def test_the_identity_preimage_states_only_event_time_facts():
    """A guard on the preimage itself: adding a field that a later
    lifecycle step can change would silently reintroduce report#283, so
    the member set is pinned and must be changed deliberately."""

    import inspect

    from app.reporting_jobs import portfolio_memory_events as builder

    source = inspect.getsource(builder._build_event)
    preimage = source.split("safe_payload = {", 1)[1].split("}", 1)[0]
    stated = sorted(line.split('"')[1] for line in preimage.splitlines() if '":' in line)

    assert stated == [
        "created_at",
        "event_type",
        "identity_preimage_version",
        "portfolio_id",
        "report_job_id",
        "report_type",
        "status",
        "status_event_id",
    ]
