from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Mapping

from app.idea_evidence_intake.models import (
    IdeaEvidencePackIntakeRequest,
    IdeaEvidencePackIntakeResponse,
    IdeaEvidencePackMaterializationRequest,
)
from app.reporting_jobs.models import ProofPackReportJobRequest

REPORT_IDEA_EVIDENCE_INTAKE_ROUTE = "POST /reports/idea-evidence-packs"
REPORT_IDEA_EVIDENCE_INTAKE_BLOCKERS = (
    "report_evidence_pack_live_materialization_proof_missing",
    "rendered_output_creation_missing",
    "archive_record_creation_missing",
    "client_publication_authority_blocked",
)
REPORT_IDEA_EVIDENCE_INTAKE_EVIDENCE_REFS = (
    "POST /reports/idea-evidence-packs",
    "contracts/idea-evidence-intake/lotus-report-idea-evidence-pack-intake.v1.json",
    "src/app/idea_evidence_intake/service.py",
    "src/app/routers/idea_evidence_intake.py",
    "tests/unit/test_idea_evidence_intake_service.py",
    "tests/integration/test_idea_evidence_intake_api.py",
)

IDEA_EVIDENCE_MATERIALIZATION_ROUTE = "POST /reports/idea-evidence-packs/materializations"
IDEA_EVIDENCE_MATERIALIZATION_EVIDENCE_REFS = (
    "POST /reports/idea-evidence-packs/materializations",
    "contracts/idea-evidence-materialization/"
    "lotus-report-idea-evidence-pack-materialization.v1.json",
    "src/app/idea_evidence_intake/service.py",
    "src/app/routers/idea_evidence_intake.py",
    "src/app/reporting_lineage/capture_service.py",
    "src/app/reporting_render/package_builder.py",
    "tests/unit/test_idea_evidence_materialization_contract.py",
    "tests/unit/test_idea_evidence_intake_service.py",
    "tests/integration/test_idea_evidence_intake_api.py",
)


class IdeaEvidenceIntakeConflictError(ValueError):
    pass


@dataclass(frozen=True)
class IdeaEvidenceIntakeRecord:
    intake_id: str
    idempotency_key: str
    payload_fingerprint: str
    response: IdeaEvidencePackIntakeResponse


class IdeaEvidenceIntakeLedger:
    def __init__(self) -> None:
        self._records_by_key: dict[str, IdeaEvidenceIntakeRecord] = {}

    def accept(
        self,
        request: IdeaEvidencePackIntakeRequest,
        *,
        idempotency_key: str,
        accepted_at_utc: datetime | None = None,
        correlation_id: str | None = None,
    ) -> IdeaEvidencePackIntakeResponse:
        payload_fingerprint = _payload_fingerprint(request)
        existing = self._records_by_key.get(idempotency_key)
        if existing:
            if existing.payload_fingerprint != payload_fingerprint:
                raise IdeaEvidenceIntakeConflictError("idea evidence intake payload changed")
            return existing.response

        intake_id = _intake_id(idempotency_key, payload_fingerprint)
        response = IdeaEvidencePackIntakeResponse(
            intake_id=intake_id,
            intake_status="accepted",
            report_evidence_pack_id=request.report_evidence_pack_id,
            conversion_intent_id=request.conversion_intent_id,
            candidate_id=request.candidate_id,
            producer="lotus-idea",
            owned_product="lotus-report:ClientReportEvidencePack:v1",
            supportability_status="not_certified",
            route_existence_proven=True,
            materialization_proven=False,
            creates_report_job=False,
            creates_rendered_output=False,
            creates_archive_record=False,
            grants_client_publication_authority=False,
            remaining_blockers=REPORT_IDEA_EVIDENCE_INTAKE_BLOCKERS,
            evidence_refs=REPORT_IDEA_EVIDENCE_INTAKE_EVIDENCE_REFS,
            accepted_at_utc=accepted_at_utc or datetime.now(UTC),
            correlation_id=correlation_id,
        )
        self._records_by_key[idempotency_key] = IdeaEvidenceIntakeRecord(
            intake_id=intake_id,
            idempotency_key=idempotency_key,
            payload_fingerprint=payload_fingerprint,
            response=response,
        )
        return response

    def snapshot(self) -> Mapping[str, IdeaEvidenceIntakeRecord]:
        return MappingProxyType(dict(self._records_by_key))


def _payload_fingerprint(request: IdeaEvidencePackIntakeRequest) -> str:
    payload = request.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _intake_id(idempotency_key: str, payload_fingerprint: str) -> str:
    digest = hashlib.sha256(f"{idempotency_key}:{payload_fingerprint}".encode("utf-8")).hexdigest()
    return "idea_intake_" + digest[:24]


def build_proof_pack_report_job_request_from_idea_evidence(
    request: IdeaEvidencePackMaterializationRequest,
) -> ProofPackReportJobRequest:
    evidence_pack = request.idea_evidence_pack
    proof_pack_input = {
        "contract_version": "1.0",
        "source_contract_version": "lotus_idea_evidence_pack_report_input.v1",
        "proof_pack_id": evidence_pack.report_evidence_pack_id,
        "proof_pack_content_hash": evidence_pack.evidence_content_fingerprint,
        "portfolio_id": request.portfolio_id,
        "mandate_id": request.mandate_id or "not_available",
        "as_of_date": request.as_of_date,
        "generated_at": evidence_pack.requested_at_utc.isoformat(),
        "report_title": f"Idea Evidence Pack - {evidence_pack.report_evidence_pack_id}",
        "report_audience": ["advisor", "investment_control", "audit"],
        "state": "READY_FOR_REPORT_MATERIALIZATION",
        "decision_summary": {
            "recommended_action": "review_opportunity_evidence",
            "rationale": ", ".join(evidence_pack.reason_codes),
        },
        "supportability": {
            "status": "READY",
            "reason_codes": tuple(evidence_pack.reason_codes),
        },
        "sections": _source_summary_sections(evidence_pack),
        "markdown_summary": _markdown_summary(evidence_pack),
        "source_hashes": {
            "idea_evidence_packet": evidence_pack.evidence_content_fingerprint,
        },
        "redaction_policy": "NO_RAW_PAYLOADS",
        "evidence_ref": {
            "source_system": "lotus-idea",
            "source_type": "LOTUS_IDEA_EVIDENCE_PACK_REPORT_INPUT",
            "source_id": (
                f"{evidence_pack.report_evidence_pack_id}:lotus_idea_evidence_pack_report_input"
            ),
            "content_hash": evidence_pack.evidence_content_fingerprint,
        },
        "content_hash": evidence_pack.evidence_content_fingerprint,
        "source_lineage": [
            {
                "source_system": "lotus-idea",
                "source_type": "IdeaEvidencePacket",
                "source_id": evidence_pack.evidence_packet_id,
                "content_hash": evidence_pack.evidence_content_fingerprint,
            }
        ],
        "client_publication_authority_granted": False,
    }
    return ProofPackReportJobRequest(
        proof_pack_report_input=proof_pack_input,
        requested_output_formats=request.requested_output_formats,
        reporting_currency=request.reporting_currency,
        options=request.options,
    )


def _source_summary_sections(
    request: IdeaEvidencePackIntakeRequest,
) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    for index, summary in enumerate(request.source_summaries, start=1):
        section_id = f"idea_source_{index}"
        sections.append(
            {
                "section_id": section_id,
                "section_type": "IDEA_SOURCE_EVIDENCE",
                "state": "READY",
                "title": f"{summary.source_system} evidence summary",
                "summary": (
                    f"{summary.product_id} {summary.product_version} as of "
                    f"{summary.as_of_date}: {summary.data_quality_status}, "
                    f"{summary.freshness}."
                ),
                "reason_codes": tuple(request.reason_codes),
                "facts": {},
                "metrics": {},
                "evidence_refs": [
                    {
                        "source_system": summary.source_system,
                        "product_id": summary.product_id,
                        "product_version": summary.product_version,
                        "as_of_date": summary.as_of_date,
                    }
                ],
                "source_refs": [
                    {
                        "source_system": summary.source_system,
                        "product_id": summary.product_id,
                    }
                ],
                "content_hash": request.evidence_content_fingerprint,
            }
        )
    return sections


def _markdown_summary(request: IdeaEvidencePackIntakeRequest) -> str:
    reason_codes = ", ".join(request.reason_codes)
    source_count = len(request.source_summaries)
    return (
        "# Idea Evidence Pack\n\n"
        f"- Report evidence pack: {request.report_evidence_pack_id}\n"
        f"- Evidence packet: {request.evidence_packet_id}\n"
        f"- Source summary count: {source_count}\n"
        f"- Reason codes: {reason_codes}\n"
        "- Client publication authority: blocked\n"
    )
