from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from app.idea_evidence_intake.materialization_contract import (
    IDEA_EVIDENCE_MATERIALIZATION_EVIDENCE_REFS,
    IDEA_EVIDENCE_MATERIALIZATION_REMAINING_BLOCKERS,
    IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION,
)
from app.idea_evidence_intake.models import (
    IdeaEvidenceMaterializationRecoveryIdentity,
    IdeaEvidencePackMaterializationRequest,
    IdeaEvidencePackMaterializationResponse,
)
from app.reporting_jobs.models import (
    ReportJobLedgerRecord,
    ReportJobListFilters,
    ReportJobOwnerSnapshot,
)


class IdeaMaterializationNotFoundError(LookupError):
    pass


class IdeaMaterializationIdentityConflictError(ValueError):
    pass


class ReportJobRecoveryReader(Protocol):
    def list_job_owner_snapshots(
        self, *, filters: ReportJobListFilters
    ) -> list[ReportJobOwnerSnapshot]: ...


def recovery_identity_from_request(
    request: IdeaEvidencePackMaterializationRequest,
) -> IdeaEvidenceMaterializationRecoveryIdentity:
    evidence_pack = request.idea_evidence_pack
    return IdeaEvidenceMaterializationRecoveryIdentity(
        report_evidence_pack_id=evidence_pack.report_evidence_pack_id,
        conversion_intent_id=evidence_pack.conversion_intent_id,
        candidate_id=evidence_pack.candidate_id,
        evidence_packet_id=evidence_pack.evidence_packet_id,
        evidence_content_fingerprint=evidence_pack.evidence_content_fingerprint,
        portfolio_id=request.portfolio_id,
    )


def recover_idea_materialization(
    *,
    ledger: ReportJobRecoveryReader,
    tenant_id: str,
    idempotency_key: str,
    expected_identity: IdeaEvidenceMaterializationRecoveryIdentity,
) -> IdeaEvidencePackMaterializationResponse:
    snapshots = ledger.list_job_owner_snapshots(
        filters=ReportJobListFilters(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            limit=2,
        )
    )
    if not snapshots:
        raise IdeaMaterializationNotFoundError("idea_materialization_not_found")
    if len(snapshots) != 1:
        raise IdeaMaterializationIdentityConflictError("idea_materialization_ambiguous")

    snapshot = snapshots[0]
    record = snapshot.record
    stored_identity = _stored_recovery_identity(record)
    if stored_identity != expected_identity:
        raise IdeaMaterializationIdentityConflictError("idea_materialization_identity_changed")
    _validate_record_binding(record, stored_identity)
    return materialization_response(
        record=record,
        source_event_version=snapshot.source_event_version,
        identity=stored_identity,
        idempotency_key=idempotency_key,
    )


def materialization_response(
    *,
    record: ReportJobLedgerRecord,
    source_event_version: int,
    identity: IdeaEvidenceMaterializationRecoveryIdentity,
    idempotency_key: str,
) -> IdeaEvidencePackMaterializationResponse:
    return IdeaEvidencePackMaterializationResponse(
        report_request_id=record.request_id,
        report_job_id=record.job_id,
        status=record.status,
        materialization_status=record.status,
        source_event_version=source_event_version,
        status_url=f"/reports/jobs/{record.job_id}",
        idempotency_key=idempotency_key,
        report_package_identity=identity.model_dump(exclude={"portfolio_id"}),
        creates_rendered_output=record.render_job_id is not None,
        creates_archive_record=record.archive_document_id is not None,
        remaining_blockers=IDEA_EVIDENCE_MATERIALIZATION_REMAINING_BLOCKERS,
        evidence_refs=IDEA_EVIDENCE_MATERIALIZATION_EVIDENCE_REFS,
        render_job_id=record.render_job_id,
        archive_document_id=record.archive_document_id,
    )


def _stored_recovery_identity(
    record: ReportJobLedgerRecord,
) -> IdeaEvidenceMaterializationRecoveryIdentity:
    raw_identity = record.options.get(IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION)
    try:
        identity: IdeaEvidenceMaterializationRecoveryIdentity = (
            IdeaEvidenceMaterializationRecoveryIdentity.model_validate(raw_identity)
        )
        return identity
    except ValidationError as exc:
        raise IdeaMaterializationIdentityConflictError(
            "idea_materialization_identity_invalid"
        ) from exc


def _validate_record_binding(
    record: ReportJobLedgerRecord,
    identity: IdeaEvidenceMaterializationRecoveryIdentity,
) -> None:
    proof_pack = record.options.get("proof_pack_report_input")
    portfolio_ids = record.portfolio_scope.get("portfolio_ids")
    if (
        record.report_type != "proof_pack"
        or record.caller_application != "lotus-idea"
        or portfolio_ids != [identity.portfolio_id]
        or not isinstance(proof_pack, dict)
        or proof_pack.get("proof_pack_id") != identity.report_evidence_pack_id
        or proof_pack.get("proof_pack_content_hash") != identity.evidence_content_fingerprint
        or proof_pack.get("source_contract_version") != identity.source_contract_version
    ):
        raise IdeaMaterializationIdentityConflictError("idea_materialization_record_inconsistent")
