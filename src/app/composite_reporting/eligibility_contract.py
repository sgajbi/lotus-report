"""Transport and custody checks for source-owned eligibility, without rule evaluation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.models import (
    Digest,
    EligibilitySelection,
    Identifier,
    SourceModel,
    SourceNumber,
)
from app.composite_reporting.table_contract import TableModel, require_complete_table


class EligibilityAssessment(SourceModel):
    rule: Literal["SIGNIFICANT_FLOW", "CASH", "READINESS"]
    outcome: Literal["PASS", "FAIL", "UNKNOWN"]
    failure_reasons: list[str]
    unknown_reasons: list[str]
    numerator: SourceNumber | None
    denominator: SourceNumber | None
    ratio: SourceNumber | None
    gross_inflow: SourceNumber | None
    gross_outflow: SourceNumber | None
    admitted_flow_count: int | None = Field(ge=0, le=250, strict=True)


class EligibilityMember(SourceModel):
    portfolio_id: Identifier
    observations_present: StrictBool
    status: Literal["INCLUDED", "EXCLUDED", "PENDING_REVIEW"]
    assessments: list[EligibilityAssessment] = Field(min_length=3, max_length=3)


class EligibilityEvaluation(SourceModel):
    product_name: Literal["CompositeMonthlyEligibilityEvaluation"]
    product_version: Literal["v1"]
    evidence_class: Literal["SYNTHETIC_UNQUALIFIED", "SOURCE_UNVERIFIED"]
    official_activation: Literal["UNAVAILABLE"]
    population_verification: Literal["UNVERIFIED"]
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    month: str
    source_cut_id: Identifier
    source_revision: Identifier
    evaluated_at: str
    input_content_hash: Digest
    universe_content_hash: Digest
    resolved_policy: dict[str, Any]
    declared_universe_coverage: Literal["COMPLETE", "INCOMPLETE"]
    expected_count: int = Field(ge=1, le=1000, strict=True)
    observed_count: int = Field(ge=0, le=1000, strict=True)
    included_count: int = Field(ge=0, le=1000, strict=True)
    excluded_count: int = Field(ge=0, le=1000, strict=True)
    pending_review_count: int = Field(ge=0, le=1000, strict=True)
    portfolios: list[EligibilityMember] = Field(min_length=1, max_length=1000)
    content_hash: Digest


class EligibilityPolicy(SourceModel):
    product_name: Literal["CompositeMonthlyEligibilityPolicy"]
    product_version: Literal["v1"]
    profile_kind: Literal["SYNTHETIC_MONTHLY_ABS_NET_CASH_READINESS"]
    official_activation: Literal["UNAVAILABLE"]
    month: str
    scope: dict[str, Any]
    layers: list[dict[str, Any]] = Field(min_length=1, max_length=5)
    flow_threshold: SourceNumber
    cash_threshold: SourceNumber
    membership_frequency: Literal["CALENDAR_MONTH"]
    flow_measure: Literal["ABS_NET"]
    flow_denominator: Literal["PRIOR_MONTH_END_NET_ASSETS"]
    flow_breach_operator: Literal["GREATER_THAN_OR_EQUAL"]
    cash_denominator: Literal["MONTH_END_NET_ASSETS"]
    cash_numerator: Literal["SETTLED_UNENCUMBERED_CASH_ONLY"]
    cash_breach_operator: Literal["GREATER_THAN"]
    ratio_unit: Literal["DECIMAL_FRACTION"]
    flow_date_basis: Literal["SOURCE_BUSINESS_DATE_UTC"]
    holiday_treatment: Literal["NO_DATE_SHIFT"]
    currency_treatment: Literal["SOURCE_NORMALIZED_SINGLE_CURRENCY"]
    observation_window: Literal["WHOLE_TARGET_MONTH"]
    evaluation_timing: Literal["AFTER_MONTH_END"]
    source_cut_timing: Literal["AFTER_MONTH_END"]
    missing_data: Literal["REQUIRED_UNKNOWN"]
    reentry: Literal["REEVALUATE_ALL_NEXT_MONTH_RULES"]
    content_hash: Digest


class EligibilityObservationMember(SourceModel):
    portfolio_id: Identifier
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    prior_month_end_assets: SourceNumber | None
    prior_assets_as_of: str | None
    month_end_assets: SourceNumber | None
    settled_unencumbered_cash: SourceNumber | None
    cash_as_of: str | None
    discretionary: StrictBool | None
    funded: StrictBool | None
    invested: StrictBool | None
    readiness_as_of: str | None
    flow_coverage_from: str | None
    flow_coverage_to: str | None
    flows: list[dict[str, Any]] = Field(max_length=250)


class EligibilityObservations(SourceModel):
    product_name: Literal["CompositeMonthlyEligibilityObservations"]
    product_version: Literal["v1"]
    evidence_class: Literal["SYNTHETIC_UNQUALIFIED", "SOURCE_UNVERIFIED"]
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    month: str
    source_cut_id: Identifier
    source_revision: Identifier
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    source_generated_at: str
    expected_portfolio_ids: list[Identifier] = Field(min_length=1, max_length=1000)
    portfolios: list[EligibilityObservationMember] = Field(max_length=1000)


class EligibilityProposal(SourceModel):
    product_name: Literal["CompositeMonthlyEvaluationProposal"]
    product_version: Literal["v1"]
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


class EligibilityApproval(SourceModel):
    product_name: Literal["CompositeMonthlyEvaluationApproval"]
    product_version: Literal["v1"]
    evidence_kind: Literal["SYNTHETIC_UNSIGNED"]
    official_activation: Literal["UNAVAILABLE"]
    proposal: EligibilityProposal
    approved_by: Identifier
    approved_at: str
    claims_digest: Digest
    membership_content_hash: Digest
    published_universe_content_hash: Digest
    content_hash: Digest


class EligibilityReceipt(SourceModel):
    product_name: Literal["CompositeMonthlyEligibilityPublicationReceipt"]
    product_version: Literal["v1"]
    definition: dict[str, Any]
    approval: EligibilityApproval
    membership_binding: dict[str, str]
    universe_binding: dict[str, str]
    source_cut_id: Identifier
    publication_sequence: int = Field(gt=0, strict=True)
    completeness: Literal["UNVERIFIED"]
    content_hash: Digest


class MembershipDecision(SourceModel):
    portfolio_id: Identifier
    effective_from: str
    effective_to: str | None
    status: Literal["INCLUDED", "EXCLUDED", "PENDING_REVIEW"]
    reason_code: str | None
    discretionary: StrictBool
    approval_ref: str | None
    source_snapshot_id: Identifier


class EligibilityMembership(SourceModel):
    product_name: Literal["CompositeMembership"]
    product_version: Literal["v1"]
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    membership_revision: Identifier
    policy_version: Identifier
    source_cut_id: Identifier
    decisions: list[MembershipDecision] = Field(min_length=1)
    decided_at: str
    decided_by: Identifier
    correlation_id: Identifier
    supersedes_membership_revision: str | None
    affected_from: str | None
    affected_to: str | None
    content_hash: Digest


class EligibilityUniverse(SourceModel):
    product_name: Literal["CompositeUniverseAttestation"]
    product_version: Literal["v1"]
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    membership_revision: Identifier
    membership_content_hash: Digest
    attestation_version: Identifier
    coverage_from: str
    coverage_to: str
    policy_version: Identifier
    source_cut_id: Identifier
    source_products: list[dict[str, Any]] = Field(min_length=1)
    posture: Literal["COMPLETE", "INCOMPLETE", "UNAVAILABLE"]
    expected_portfolio_ids: list[Identifier]
    expected_portfolio_count: int = Field(ge=0, strict=True)
    observed_portfolio_count: int = Field(ge=0, strict=True)
    missing_portfolio_ids: list[Identifier]
    unexpected_portfolio_ids: list[Identifier]
    coverage_gap_portfolio_ids: list[Identifier]
    reason_code: str | None
    attested_at: str
    attested_by: Identifier
    correlation_id: Identifier
    content_hash: Digest


class EligibilityPublication(SourceModel):
    product_name: Literal["CompositeMembershipPublication"]
    product_version: Literal["v1"]
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    sequence: int = Field(gt=0, strict=True)
    membership_revision: Identifier
    membership_content_hash: Digest
    policy_version: Identifier
    source_cut_id: Identifier
    decision_count: int = Field(ge=1, strict=True)
    supersedes_membership_revision: str | None
    affected_from: str | None
    affected_to: str | None
    decided_at: str
    published_at: str
    completeness: Literal["UNVERIFIED"]


class EligibilityColumn(TableModel):
    column_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    value_type: Literal["TEXT", "DECIMAL_FACTOR", "MONEY", "COUNT", "BOOLEAN"]
    unit: Literal[
        "TEXT", "DECIMAL_RATIO", "CURRENCY_UNITS", "PORTFOLIO_COUNT", "EVENT_COUNT", "BOOLEAN"
    ]
    display_unit: Literal[
        "TEXT", "DECIMAL_RATIO", "CURRENCY_UNITS", "PORTFOLIO_COUNT", "EVENT_COUNT", "BOOLEAN"
    ]
    display_conversion: Literal["IDENTITY"]
    display_decimal_places: int | None = Field(ge=0, le=12)
    display_rounding_mode: Literal["HALF_UP"]
    currency: str | None = Field(pattern=r"^[A-Z]{3}$")
    scale: Literal["1"]


class EligibilityCell(TableModel):
    canonical_value: str | None
    availability: Literal["AVAILABLE", "UNAVAILABLE", "NOT_APPLICABLE"]
    reason_codes: list[str]
    source_pointer: str = Field(pattern=r"^/(source_months|selection|report_facts)/")


class EligibilityRow(TableModel):
    row_id: str = Field(min_length=1, max_length=256)
    cells: dict[str, EligibilityCell]


class EligibilityTable(TableModel):
    table_id: str = Field(min_length=1, max_length=31)
    title: str = Field(min_length=1, max_length=256)
    columns: list[EligibilityColumn] = Field(min_length=1, max_length=32)
    rows: list[EligibilityRow] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def require_rows(self) -> EligibilityTable:
        require_complete_table(
            [column.column_id for column in self.columns],
            [(row.row_id, set(row.cells)) for row in self.rows],
        )
        return self


class EvaluatedSourceMonth(TableModel):
    evidence_kind: Literal["EVALUATED_ONLY"]
    proposal: dict[str, Any]
    response_digests: dict[str, Digest]


class PublishedSourceMonth(TableModel):
    evidence_kind: Literal["PUBLISHED"]
    receipt: dict[str, Any]
    membership: dict[str, Any]
    universe: dict[str, Any]
    parent_membership: dict[str, Any]
    publication: dict[str, Any]
    response_digests: dict[str, Digest]


def proposal_for_month(month: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if month["evidence_kind"] == "PUBLISHED":
        return month["receipt"]["approval"]["proposal"], "receipt/approval/proposal"
    return month["proposal"], "proposal"


class CompositeEligibilityReportData(TableModel):
    contract_version: Literal["composite_review.v4"]
    qualification: Literal["CONTROLLED_ELIGIBILITY_SOURCE_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    tenant_id: Identifier
    selection: EligibilitySelection
    source_months: list[EvaluatedSourceMonth | PublishedSourceMonth] = Field(
        min_length=1, max_length=120
    )
    report_facts: dict[str, Any]
    tables: list[EligibilityTable] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def require_source_and_projection(self) -> CompositeEligibilityReportData:
        from app.composite_reporting.eligibility_admission import require_source_months
        from app.composite_reporting.eligibility_tables import eligibility_facts, eligibility_tables

        raw = self.model_dump(mode="json")
        if self.tenant_id != self.selection.tenant_id:
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_TENANT_CONFLICT")
        require_source_months(self.selection, raw["source_months"])
        if self.report_facts != eligibility_facts(raw) or raw["tables"] != eligibility_tables(raw):
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_COMPLETE_PROJECTION_CONFLICT")
        return self


def composite_eligibility_report_schema() -> dict[str, Any]:
    schema = CompositeEligibilityReportData.model_json_schema()
    for model in (
        EligibilityProposal,
        EligibilityReceipt,
        EligibilityMembership,
        EligibilityUniverse,
        EligibilityPublication,
        EligibilityPolicy,
        EligibilityObservations,
    ):
        source_schema = model.model_json_schema()
        schema["$defs"].update(source_schema.pop("$defs", {}))
        schema["$defs"][model.__name__] = source_schema
    schema["$defs"]["EligibilityProposal"]["properties"]["observations"] = {
        "$ref": "#/$defs/EligibilityObservations"
    }
    schema["$defs"]["EligibilityEvaluation"]["properties"]["resolved_policy"] = {
        "$ref": "#/$defs/EligibilityPolicy"
    }
    schema["$defs"]["EvaluatedSourceMonth"]["properties"]["proposal"] = {
        "$ref": "#/$defs/EligibilityProposal"
    }
    for key, model in (
        ("receipt", EligibilityReceipt),
        ("membership", EligibilityMembership),
        ("parent_membership", EligibilityMembership),
        ("universe", EligibilityUniverse),
        ("publication", EligibilityPublication),
    ):
        schema["$defs"]["PublishedSourceMonth"]["properties"][key] = {
            "$ref": f"#/$defs/{model.__name__}"
        }
    return schema
