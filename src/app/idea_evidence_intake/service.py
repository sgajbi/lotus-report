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
)

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
