from __future__ import annotations

from fastapi.testclient import TestClient

from app.idea_evidence_intake.service import IdeaEvidenceIntakeLedger
from app.main import app
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
