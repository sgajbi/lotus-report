from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.idea_evidence_intake.service import IdeaEvidenceIntakeLedger
from app.main import app
from app.reporting_jobs.ledger import MissingIdempotencyKeyError, ReportJobLedger
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportUpstreamCallCreateRequest,
)
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.service import (
    PortfolioReviewRenderOrchestrationService,
    get_portfolio_review_render_orchestration_service,
)
from app.routers.idea_evidence_intake import get_idea_evidence_intake_ledger


def test_idea_evidence_intake_route_accepts_handoff_without_materialization() -> None:
    ledger = IdeaEvidenceIntakeLedger()
    app.dependency_overrides[get_idea_evidence_intake_ledger] = lambda: ledger
    client = TestClient(app)
    try:
        response = client.post(
            "/reports/idea-evidence-packs",
            json=_payload(),
            headers=_headers("idea-report-intake-001"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["intake_status"] == "accepted"
    assert body["route_existence_proven"] is True
    assert body["materialization_proven"] is False
    assert body["creates_report_job"] is False
    assert body["creates_rendered_output"] is False
    assert body["creates_archive_record"] is False
    assert body["grants_client_publication_authority"] is False
    assert body["supportability_status"] == "not_certified"
    assert body["correlation_id"] == "corr-idea-report-intake"
    assert "POST /reports/idea-evidence-packs" in body["evidence_refs"]


def test_idea_evidence_intake_route_replays_same_idempotency_key() -> None:
    ledger = IdeaEvidenceIntakeLedger()
    app.dependency_overrides[get_idea_evidence_intake_ledger] = lambda: ledger
    client = TestClient(app)
    try:
        first = client.post(
            "/reports/idea-evidence-packs",
            json=_payload(),
            headers=_headers("idea-report-intake-001"),
        )
        second = client.post(
            "/reports/idea-evidence-packs",
            json=_payload(),
            headers=_headers("idea-report-intake-001"),
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()


def test_idea_evidence_intake_route_conflicts_on_changed_payload_replay() -> None:
    ledger = IdeaEvidenceIntakeLedger()
    app.dependency_overrides[get_idea_evidence_intake_ledger] = lambda: ledger
    client = TestClient(app)
    changed_payload = {**_payload(), "report_evidence_pack_id": "irep_changed"}
    try:
        first = client.post(
            "/reports/idea-evidence-packs",
            json=_payload(),
            headers=_headers("idea-report-intake-001"),
        )
        second = client.post(
            "/reports/idea-evidence-packs",
            json=changed_payload,
            headers=_headers("idea-report-intake-001"),
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "idea_evidence_intake_conflict"


def test_idea_evidence_intake_route_rejects_publication_or_render_claims() -> None:
    client = TestClient(app)
    payload = {
        **_payload(),
        "grants_client_publication_authority": True,
        "creates_rendered_output": True,
        "creates_archive_record": True,
    }

    response = client.post(
        "/reports/idea-evidence-packs",
        json=payload,
        headers=_headers("idea-report-intake-unsafe"),
    )

    assert response.status_code == 422


def test_idea_evidence_intake_route_requires_idempotency_key() -> None:
    client = TestClient(app)
    headers = _headers("idea-report-intake-missing-key")
    headers.pop("Idempotency-Key")

    response = client.post("/reports/idea-evidence-packs", json=_payload(), headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_idempotency_key"


def test_idea_evidence_materialization_route_creates_archived_report_job(tmp_path) -> None:
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    lineage_store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    intake_ledger = IdeaEvidenceIntakeLedger()
    capture_service = _IdeaEvidenceCaptureService(ledger, lineage_store)
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=_SuccessfulRenderClient(),
        archive_client=_SuccessfulArchiveClient(),
        snapshot_store=lineage_store,
        job_ledger=ledger,
    )
    app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    app.dependency_overrides[get_idea_evidence_intake_ledger] = lambda: intake_ledger
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        render_service
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/reports/idea-evidence-packs/materializations",
            json=_materialization_payload(),
            headers=_headers("idea-report-materialization-001"),
        )
        replay = client.post(
            "/reports/idea-evidence-packs/materializations",
            json=_materialization_payload(),
            headers=_headers("idea-report-materialization-001"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert replay.status_code == 202
    body = response.json()
    assert replay.json() == body
    assert body["status"] == "archived"
    record = ledger.get_job(body["report_job_id"])
    assert record.report_type == "proof_pack"
    assert record.archive_document_id == "doc_idea_evidence_pack_001"
    snapshot = lineage_store.get_snapshot_by_job(body["report_job_id"])
    assert snapshot.lineage_summary["source_services"] == ["lotus-idea"]
    upstream_calls = lineage_store.list_upstream_calls(snapshot.snapshot_id)
    assert upstream_calls[0].service_name == "lotus-idea"
    assert upstream_calls[0].endpoint == "/reports/idea-evidence-packs/materializations"
    assert upstream_calls[0].contract_version == "LotusIdeaEvidencePackReportInput.1.0"


def test_idea_evidence_materialization_route_can_capture_json_only_proof(tmp_path) -> None:
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    lineage_store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    intake_ledger = IdeaEvidenceIntakeLedger()
    capture_service = _IdeaEvidenceCaptureService(ledger, lineage_store)
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=_UnexpectedRenderClient(),
        archive_client=_UnexpectedArchiveClient(),
        snapshot_store=lineage_store,
        job_ledger=ledger,
    )
    app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    app.dependency_overrides[get_idea_evidence_intake_ledger] = lambda: intake_ledger
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        render_service
    )
    payload = {**_materialization_payload(), "requested_output_formats": ["json"]}
    client = TestClient(app)
    try:
        response = client.post(
            "/reports/idea-evidence-packs/materializations",
            json=payload,
            headers=_headers("idea-report-materialization-json-only"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "data_ready"
    record = ledger.get_job(body["report_job_id"])
    assert record.requested_output_formats == ["json"]
    assert record.archive_document_id is None
    snapshot = lineage_store.get_snapshot_by_job(body["report_job_id"])
    assert snapshot.lineage_summary["source_type"] == "LOTUS_IDEA_EVIDENCE_PACK_REPORT_INPUT"


def test_idea_evidence_materialization_route_requires_idempotency_key() -> None:
    client = TestClient(app)
    headers = _headers("idea-report-materialization-missing-key")
    headers.pop("Idempotency-Key")

    response = client.post(
        "/reports/idea-evidence-packs/materializations",
        json=_materialization_payload(),
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_idempotency_key"


def test_idea_evidence_materialization_route_conflicts_on_changed_payload_replay(
    tmp_path,
) -> None:
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    lineage_store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    intake_ledger = IdeaEvidenceIntakeLedger()
    capture_service = _IdeaEvidenceCaptureService(ledger, lineage_store)
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=_SuccessfulRenderClient(),
        archive_client=_SuccessfulArchiveClient(),
        snapshot_store=lineage_store,
        job_ledger=ledger,
    )
    app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    app.dependency_overrides[get_idea_evidence_intake_ledger] = lambda: intake_ledger
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        render_service
    )
    changed_payload = {
        **_materialization_payload(),
        "idea_evidence_pack": {
            **_payload(),
            "report_evidence_pack_id": "irep_changed",
        },
    }
    client = TestClient(app)
    try:
        first = client.post(
            "/reports/idea-evidence-packs/materializations",
            json=_materialization_payload(),
            headers=_headers("idea-report-materialization-conflict"),
        )
        second = client.post(
            "/reports/idea-evidence-packs/materializations",
            json=changed_payload,
            headers=_headers("idea-report-materialization-conflict"),
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "idempotency_conflict"


def test_idea_evidence_materialization_route_maps_ledger_missing_key_error() -> None:
    app.dependency_overrides[get_report_job_ledger] = lambda: _MissingKeyReportJobLedger()
    app.dependency_overrides[get_idea_evidence_intake_ledger] = lambda: IdeaEvidenceIntakeLedger()
    client = TestClient(app)
    try:
        response = client.post(
            "/reports/idea-evidence-packs/materializations",
            json=_materialization_payload(),
            headers=_headers("idea-report-materialization-ledger-missing-key"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_idempotency_key"


def test_idea_evidence_materialization_route_keeps_publication_blocked() -> None:
    client = TestClient(app)
    payload = {
        **_materialization_payload(),
        "grants_client_publication_authority": True,
    }

    response = client.post(
        "/reports/idea-evidence-packs/materializations",
        json=payload,
        headers=_headers("idea-report-materialization-unsafe"),
    )

    assert response.status_code == 422


def _payload() -> dict[str, object]:
    return {
        "report_evidence_pack_id": "irep_001",
        "conversion_intent_id": "icnv_001",
        "candidate_id": "icand_001",
        "purpose": "CLIENT_REPORT_EVIDENCE",
        "evidence_packet_id": "ievp_001",
        "evidence_content_fingerprint": "sha256:idea-evidence-content",
        "source_signal_ids": ["sig_high_cash_001"],
        "source_summaries": [
            {
                "product_id": "lotus-core:HoldingsAsOf:v1",
                "source_system": "lotus-core",
                "product_version": "v1",
                "as_of_date": "2026-06-24",
                "generated_at_utc": "2026-06-24T08:00:00Z",
                "data_quality_status": "complete",
                "freshness": "fresh",
            }
        ],
        "reason_codes": ["HIGH_CASH_REVIEWED_FOR_REPORT"],
        "report_source_authority": "lotus-report",
        "render_source_authority": "lotus-render",
        "archive_source_authority": "lotus-archive",
        "boundary": "REPORT_INTAKE_ONLY",
        "retention_policy_ref": "generated-report-standard",
        "requested_at_utc": "2026-06-24T08:15:00Z",
        "grants_client_publication_authority": False,
        "creates_rendered_output": False,
        "creates_archive_record": False,
        "producer": "lotus-idea",
        "supportability_status": "not_certified",
    }


def _materialization_payload() -> dict[str, object]:
    return {
        "idea_evidence_pack": _payload(),
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "mandate_id": "MANDATE_PB_SG_GLOBAL_BAL_001",
        "as_of_date": "2026-06-24",
        "requested_output_formats": ["pdf"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
        "boundary": "REPORT_JOB_MATERIALIZATION",
        "grants_client_publication_authority": False,
        "producer": "lotus-idea",
        "supportability_status": "not_certified",
    }


def _headers(idempotency_key: str) -> dict[str, str]:
    return {
        "Idempotency-Key": idempotency_key,
        "X-Actor-Id": "advisor-123",
        "X-Caller-Application": "lotus-idea",
        "X-Tenant-Id": "tenant-sg",
        "X-Region": "APAC",
        "X-Booking-Center-Code": "SG",
        "X-Role": "advisor",
        "X-Correlation-ID": "corr-idea-report-intake",
        "X-Trace-ID": "trace-idea-report-intake",
    }


class _IdeaEvidenceCaptureService:
    def __init__(self, ledger: ReportJobLedger, lineage_store: ReportInputSnapshotStore) -> None:
        self._ledger = ledger
        self._lineage_store = lineage_store

    async def capture_for_job(self, job):
        self._ledger.mark_collecting_data(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        report_input = job.options["proof_pack_report_input"]
        snapshot = self._lineage_store.create_snapshot(
            ReportInputSnapshotCreateRequest(
                report_job_id=job.job_id,
                report_type=job.report_type,
                report_data_contract_version="dpm_proof_pack_report_input.v1",
                portfolio_scope=job.portfolio_scope,
                as_of_date=job.as_of_date,
                snapshot_payload=report_input,
                snapshot_storage_ref=None,
                supportability_status="complete",
                completeness_status="complete",
                lineage_summary={
                    "source_services": ["lotus-idea"],
                    "call_count": 1,
                    "supportability_status": "complete",
                    "completeness_status": "complete",
                    "proof_pack_id": report_input["proof_pack_id"],
                    "source_type": "LOTUS_IDEA_EVIDENCE_PACK_REPORT_INPUT",
                    "source_hash": report_input["content_hash"],
                },
                captured_at=datetime.now(UTC),
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        )
        self._lineage_store.create_upstream_calls(
            snapshot_id=snapshot.snapshot_id,
            calls=[
                ReportUpstreamCallCreateRequest(
                    service_name="lotus-idea",
                    endpoint="/reports/idea-evidence-packs/materializations",
                    method="GET",
                    contract_version="LotusIdeaEvidencePackReportInput.1.0",
                    request_hash=report_input["proof_pack_content_hash"],
                    response_hash=report_input["content_hash"],
                    response_ref=report_input["evidence_ref"]["source_id"],
                    status_code=200,
                    latency_ms=0,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            ],
        )
        return self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )


class _SuccessfulRenderClient:
    async def submit_render_package(self, payload, **kwargs):
        return 201, {
            "status": "rendered",
            "render_job_id": payload["render_job_id"],
            "artifact_sha256": "sha256:idea-evidence-rendered-pdf",
            "bounded_determinism_fingerprint": "fingerprint-idea-evidence",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 420,
            "artifact_base64": "JVBERi0xLjQ=",
        }


class _SuccessfulArchiveClient:
    async def archive_document(self, payload, **kwargs):
        return 201, {"document_id": "doc_idea_evidence_pack_001"}


class _UnexpectedRenderClient:
    async def submit_render_package(self, payload, **kwargs):
        raise AssertionError("JSON-only materialization must not call render")


class _UnexpectedArchiveClient:
    async def archive_document(self, payload, **kwargs):
        raise AssertionError("JSON-only materialization must not call archive")


class _MissingKeyReportJobLedger:
    def create_proof_pack_report_job(self, **kwargs):
        raise MissingIdempotencyKeyError("missing idempotency key")
