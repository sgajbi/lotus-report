"""Additive monthly source-v2 transport; definitions retain their independent version."""

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.eligibility_contract import (
    EligibilityEvaluation,
    EligibilityTable,
    EvaluatedSourceMonth,
    PublishedSourceMonth,
    composite_eligibility_report_schema,
)
from app.composite_reporting.models import (
    AmendmentEligibilitySelection,
    Digest,
    Identifier,
    SourceModel,
)
from app.composite_reporting.table_contract import TableModel


class MonthlyBinding(TableModel):
    product_name: Literal[
        "CompositeMonthlyEvaluationApproval",
        "CompositeMonthlyEligibilityPublicationReceipt",
        "CompositeMembership",
    ]
    product_version: Literal["v1", "v2"]
    revision: Identifier
    digest: Digest


class AmendmentEvidenceBinding(TableModel):
    product_name: Identifier
    product_version: Literal["v1"]
    revision: Identifier
    digest: Digest


class MonthlyAmendment(TableModel):
    correction_kind: Literal["SOURCE_CORRECTION"]
    predecessor_approval_binding: MonthlyBinding
    predecessor_receipt_binding: MonthlyBinding
    original_approval_binding: MonthlyBinding
    expected_authority_binding: MonthlyBinding
    projection_parent_membership_binding: MonthlyBinding
    expected_current_publication_sequence: int = Field(gt=0, strict=True)
    affected_from: date
    affected_to: date
    reason_code: Identifier
    reason: str = Field(min_length=1, max_length=2048)
    evidence_bindings: list[AmendmentEvidenceBinding] = Field(min_length=1, max_length=32)


class AmendmentProposal(SourceModel):
    product_name: Literal["CompositeMonthlyEvaluationProposal"]
    product_version: Literal["v2"]
    evaluation_revision: Identifier
    target_membership_revision: Identifier
    parent_membership_revision: Identifier
    parent_membership_content_hash: Digest
    policy_approval: dict[str, Any]
    universe: dict[str, Any]
    observations: dict[str, Any]
    evaluation: EligibilityEvaluation
    proposed_by: Identifier
    proposed_at: str
    correlation_id: Identifier
    content_hash: Digest
    amendment: MonthlyAmendment
    publication_evidence_version: Literal["v1"]
    source_assembly_evidence: dict[str, Any] = Field(min_length=1)


class AmendmentApproval(SourceModel):
    product_name: Literal["CompositeMonthlyEvaluationApproval"]
    product_version: Literal["v2"]
    evidence_kind: Literal["SYNTHETIC_UNSIGNED"]
    official_activation: Literal["UNAVAILABLE"]
    proposal: AmendmentProposal
    approved_by: Identifier
    approved_at: str
    claims_digest: Digest
    membership_content_hash: Digest
    published_universe_content_hash: Digest
    content_hash: Digest


class AmendmentReceipt(SourceModel):
    product_name: Literal["CompositeMonthlyEligibilityPublicationReceipt"]
    product_version: Literal["v2"]
    definition: dict[str, Any]
    approval: AmendmentApproval
    membership_binding: dict[str, str]
    universe_binding: dict[str, str]
    source_cut_id: Identifier
    publication_sequence: int = Field(gt=0, strict=True)
    completeness: Literal["UNVERIFIED"]
    content_hash: Digest
    lineage: MonthlyAmendment


class AmendmentEvaluatedMonth(EvaluatedSourceMonth):
    lineage_receipts: list[dict[str, Any]] = Field(min_length=1, max_length=31)


class AmendmentPublishedMonth(PublishedSourceMonth):
    lineage_receipts: list[dict[str, Any]] = Field(min_length=1, max_length=31)
    parent_publication: dict[str, Any]


class CompositeAmendmentReportData(TableModel):
    contract_version: Literal["composite_review.v6"]
    qualification: Literal["CONTROLLED_MONTHLY_SOURCE_AMENDMENT_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    tenant_id: Identifier
    selection: AmendmentEligibilitySelection
    source_months: list[AmendmentEvaluatedMonth | AmendmentPublishedMonth] = Field(
        min_length=1, max_length=120
    )
    report_facts: dict[str, Any]
    tables: list[EligibilityTable] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def require_custody(self) -> "CompositeAmendmentReportData":
        from app.composite_reporting.amendment_admission import require_amendment_months
        from app.composite_reporting.amendment_tables import amendment_facts, amendment_tables

        raw = self.model_dump(mode="json")
        if self.tenant_id != self.selection.tenant_id:
            raise CompositeEvidenceRefused("COMPOSITE_AMENDMENT_TENANT_CONFLICT")
        require_amendment_months(self.selection, raw["source_months"])
        if raw["report_facts"] != amendment_facts(raw) or raw["tables"] != amendment_tables(raw):
            raise CompositeEvidenceRefused("COMPOSITE_AMENDMENT_PROJECTION_CONFLICT")
        return self


def composite_amendment_report_schema() -> dict[str, Any]:
    schema = CompositeAmendmentReportData.model_json_schema()
    schema["$defs"].update(composite_eligibility_report_schema()["$defs"])
    for model in (AmendmentProposal, AmendmentReceipt):
        source = model.model_json_schema()
        schema["$defs"].update(source.pop("$defs", {}))
        schema["$defs"][model.__name__] = source
    schema["$defs"]["AmendmentProposal"]["properties"]["observations"] = {
        "$ref": "#/$defs/EligibilityObservations"
    }
    schema["$defs"]["AmendmentEvaluatedMonth"]["properties"]["proposal"] = {
        "$ref": "#/$defs/AmendmentProposal"
    }
    schema["$defs"]["AmendmentPublishedMonth"]["properties"]["receipt"] = {
        "$ref": "#/$defs/AmendmentReceipt"
    }
    for variant in ("AmendmentEvaluatedMonth", "AmendmentPublishedMonth"):
        schema["$defs"][variant]["properties"]["lineage_receipts"]["items"] = {
            "anyOf": [{"$ref": "#/$defs/EligibilityReceipt"}, {"$ref": "#/$defs/AmendmentReceipt"}]
        }
    for key in ("membership", "universe", "parent_membership", "publication"):
        schema["$defs"]["AmendmentPublishedMonth"]["properties"][key] = (
            composite_eligibility_report_schema()["$defs"]["PublishedSourceMonth"]["properties"][
                key
            ]
        )
    return schema
