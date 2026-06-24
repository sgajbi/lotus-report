from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.idea_evidence_intake.models import IdeaEvidencePackIntakeRequest
from app.idea_evidence_intake.service import (
    IdeaEvidenceIntakeConflictError,
    IdeaEvidenceIntakeLedger,
)


def test_idea_evidence_intake_accepts_source_safe_not_certified_handoff() -> None:
    ledger = IdeaEvidenceIntakeLedger()
    accepted_at = datetime(2026, 6, 24, 8, 30, tzinfo=UTC)

    response = ledger.accept(
        _request(),
        idempotency_key="idea-report-intake-001",
        accepted_at_utc=accepted_at,
        correlation_id="corr-idea-report-intake",
    )

    assert response.intake_status == "accepted"
    assert response.producer == "lotus-idea"
    assert response.owned_product == "lotus-report:ClientReportEvidencePack:v1"
    assert response.route_existence_proven is True
    assert response.materialization_proven is False
    assert response.creates_report_job is False
    assert response.creates_rendered_output is False
    assert response.creates_archive_record is False
    assert response.grants_client_publication_authority is False
    assert response.supportability_status == "not_certified"
    assert response.accepted_at_utc == accepted_at
    assert "rendered_output_creation_missing" in response.remaining_blockers
    assert "archive_record_creation_missing" in response.remaining_blockers
    assert "client_publication_authority_blocked" in response.remaining_blockers


def test_idea_evidence_intake_is_idempotent_for_same_payload() -> None:
    ledger = IdeaEvidenceIntakeLedger()
    request = _request()

    first = ledger.accept(request, idempotency_key="idea-report-intake-001")
    second = ledger.accept(request, idempotency_key="idea-report-intake-001")

    assert second == first
    assert len(ledger.snapshot()) == 1


def test_idea_evidence_intake_conflicts_when_idempotency_payload_changes() -> None:
    ledger = IdeaEvidenceIntakeLedger()
    ledger.accept(_request(), idempotency_key="idea-report-intake-001")

    with pytest.raises(IdeaEvidenceIntakeConflictError):
        ledger.accept(
            _request(report_evidence_pack_id="irep_changed"),
            idempotency_key="idea-report-intake-001",
        )


def _request(report_evidence_pack_id: str = "irep_001") -> IdeaEvidencePackIntakeRequest:
    return IdeaEvidencePackIntakeRequest(
        report_evidence_pack_id=report_evidence_pack_id,
        conversion_intent_id="icnv_001",
        candidate_id="icand_001",
        purpose="CLIENT_REPORT_EVIDENCE",
        evidence_packet_id="ievp_001",
        evidence_content_fingerprint="sha256:idea-evidence-content",
        source_signal_ids=("sig_high_cash_001",),
        source_summaries=(
            {
                "product_id": "lotus-core:HoldingsAsOf:v1",
                "source_system": "lotus-core",
                "product_version": "v1",
                "as_of_date": "2026-06-24",
                "generated_at_utc": "2026-06-24T08:00:00Z",
                "data_quality_status": "complete",
                "freshness": "fresh",
            },
        ),
        reason_codes=("HIGH_CASH_REVIEWED_FOR_REPORT",),
        retention_policy_ref="generated-report-standard",
        requested_at_utc=datetime(2026, 6, 24, 8, 15, tzinfo=UTC),
    )
