from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.idea_evidence_intake.materialization_contract import (
    IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION,
)
from app.idea_evidence_intake.models import IdeaEvidenceMaterializationRecoveryIdentity
from app.idea_evidence_intake.recovery import (
    IdeaMaterializationIdentityConflictError,
    recover_idea_materialization,
)
from app.reporting_jobs.models import ReportJobLedgerRecord, ReportJobOwnerSnapshot


def test_recovery_fails_closed_for_malformed_stored_identity() -> None:
    record = _record()
    record.options[IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION] = {"candidate_id": "partial"}

    with pytest.raises(
        IdeaMaterializationIdentityConflictError,
        match="idea_materialization_identity_invalid",
    ):
        recover_idea_materialization(
            ledger=_Reader([record]),
            tenant_id="tenant-sg",
            idempotency_key="idea-materialization-001",
            expected_identity=_identity(),
        )


def test_recovery_never_selects_first_row_from_ambiguous_results() -> None:
    record = _record()

    with pytest.raises(
        IdeaMaterializationIdentityConflictError,
        match="idea_materialization_ambiguous",
    ):
        recover_idea_materialization(
            ledger=_Reader([record, record.model_copy()]),
            tenant_id="tenant-sg",
            idempotency_key="idea-materialization-001",
            expected_identity=_identity(),
        )


def test_recovery_rejects_report_job_binding_drift() -> None:
    record = _record()
    record.options["proof_pack_report_input"]["proof_pack_content_hash"] = "sha256:changed"

    with pytest.raises(
        IdeaMaterializationIdentityConflictError,
        match="idea_materialization_record_inconsistent",
    ):
        recover_idea_materialization(
            ledger=_Reader([record]),
            tenant_id="tenant-sg",
            idempotency_key="idea-materialization-001",
            expected_identity=_identity(),
        )


class _Reader:
    def __init__(self, records: list[ReportJobLedgerRecord]) -> None:
        self._records = records

    def list_job_owner_snapshots(self, *, filters) -> list[ReportJobOwnerSnapshot]:
        assert filters.tenant_id == "tenant-sg"
        assert filters.idempotency_key == "idea-materialization-001"
        assert filters.limit == 2
        return [
            ReportJobOwnerSnapshot(record=record, source_event_version=1)
            for record in self._records
        ]


def _identity() -> IdeaEvidenceMaterializationRecoveryIdentity:
    return IdeaEvidenceMaterializationRecoveryIdentity(
        report_evidence_pack_id="irep_001",
        conversion_intent_id="icnv_001",
        candidate_id="icand_001",
        evidence_packet_id="ievp_001",
        evidence_content_fingerprint="sha256:idea-evidence-content",
        portfolio_id="PB_SG_GLOBAL_BAL_001",
    )


def _record() -> ReportJobLedgerRecord:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    identity = _identity()
    return ReportJobLedgerRecord(
        request_id="rrq_001",
        job_id="rjob_001",
        report_type="proof_pack",
        portfolio_scope={"portfolio_ids": [identity.portfolio_id]},
        requested_output_formats=["json"],
        as_of_date=date(2026, 6, 24),
        options={
            IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION: identity.model_dump(mode="json"),
            "proof_pack_report_input": {
                "proof_pack_id": identity.report_evidence_pack_id,
                "proof_pack_content_hash": identity.evidence_content_fingerprint,
                "source_contract_version": identity.source_contract_version,
            },
        },
        trigger_type="api",
        triggered_by="advisor-123",
        caller_application="lotus-idea",
        tenant_id="tenant-sg",
        region="APAC",
        idempotency_key="idea-materialization-001",
        request_hash="sha256:request",
        status="data_ready",
        current_step="data_ready",
        retry_eligible=False,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
        correlation_id="corr-001",
        trace_id="trace-001",
    )
