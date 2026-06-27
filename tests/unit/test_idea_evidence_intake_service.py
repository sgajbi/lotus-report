from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.idea_evidence_intake.models import (
    IdeaEvidencePackIntakeRequest,
    IdeaEvidencePackMaterializationRequest,
)
from app.idea_evidence_intake.service import (
    IdeaEvidenceIntakeConflictError,
    IdeaEvidenceIntakeLedger,
    build_proof_pack_report_job_request_from_idea_evidence,
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


def test_idea_evidence_materialization_maps_to_source_owned_proof_pack_request() -> None:
    request = IdeaEvidencePackMaterializationRequest(
        idea_evidence_pack=_request(),
        portfolio_id="PB_SG_GLOBAL_BAL_001",
        mandate_id="MANDATE_PB_SG_GLOBAL_BAL_001",
        as_of_date="2026-06-24",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"retention_policy_id": "generated-report-standard"},
    )

    report_job_request = build_proof_pack_report_job_request_from_idea_evidence(request)

    assert report_job_request.requested_output_formats == ["pdf"]
    proof_pack_input = report_job_request.proof_pack_report_input
    assert proof_pack_input["proof_pack_id"] == "irep_001"
    assert proof_pack_input["source_contract_version"] == (
        "lotus_idea_evidence_pack_report_input.v1"
    )
    assert proof_pack_input["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
    assert proof_pack_input["evidence_ref"] == {
        "source_system": "lotus-idea",
        "source_type": "LOTUS_IDEA_EVIDENCE_PACK_REPORT_INPUT",
        "source_id": "irep_001:lotus_idea_evidence_pack_report_input",
        "content_hash": "sha256:idea-evidence-content",
    }
    assert proof_pack_input["client_publication_authority_granted"] is False
    assert proof_pack_input["sections"][0]["section_type"] == "IDEA_SOURCE_EVIDENCE"


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
