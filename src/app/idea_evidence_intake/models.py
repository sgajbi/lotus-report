from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.reporting_jobs.models import ReportJobHandleResponse


class IdeaEvidenceIntakePurpose(StrEnum):
    CLIENT_REPORT_EVIDENCE = "CLIENT_REPORT_EVIDENCE"
    ADVISOR_REVIEW_APPENDIX = "ADVISOR_REVIEW_APPENDIX"


class IdeaEvidenceIntakeBoundary(StrEnum):
    REPORT_INTAKE_ONLY = "REPORT_INTAKE_ONLY"


class IdeaEvidenceMaterializationBoundary(StrEnum):
    REPORT_JOB_MATERIALIZATION = "REPORT_JOB_MATERIALIZATION"


class IdeaEvidenceMaterializationSourceAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_evidence: Literal["lotus-idea"] = "lotus-idea"
    report_materialization: Literal["lotus-report"] = "lotus-report"
    rendering: Literal["lotus-render"] = "lotus-render"
    archive_record: Literal["lotus-archive"] = "lotus-archive"
    client_publication: Literal["blocked"] = "blocked"


class IdeaEvidenceReportPackageIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_evidence_pack_id: str = Field(min_length=3)
    conversion_intent_id: str = Field(min_length=3)
    candidate_id: str = Field(min_length=3)
    evidence_packet_id: str = Field(min_length=3)
    evidence_content_fingerprint: str = Field(pattern=r"^sha256:[a-zA-Z0-9_.:-]+$")
    source_contract_version: Literal["lotus_idea_evidence_pack_report_input.v1"] = (
        "lotus_idea_evidence_pack_report_input.v1"
    )
    owned_product: Literal["lotus-report:ClientReportEvidencePack:v1"] = (
        "lotus-report:ClientReportEvidencePack:v1"
    )


class IdeaEvidenceMaterializationRecoveryIdentity(IdeaEvidenceReportPackageIdentity):
    """Exact consumer and portfolio identity bound to one Report request."""

    portfolio_id: str = Field(min_length=3)


class IdeaEvidenceSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(
        min_length=3,
        description="Source-owned product identifier cited by lotus-idea evidence.",
        examples=["lotus-core:HoldingsAsOf:v1"],
    )
    source_system: str = Field(
        min_length=3,
        description="Source authority that owns the summarized evidence.",
        examples=["lotus-core"],
    )
    product_version: str = Field(min_length=1, examples=["v1"])
    as_of_date: str = Field(
        min_length=4,
        description="Source business date or valuation date carried from the owning service.",
        examples=["2026-06-24"],
    )
    generated_at_utc: datetime = Field(
        description="UTC timestamp when the source summary was generated."
    )
    data_quality_status: str = Field(min_length=3, examples=["complete"])
    freshness: str = Field(min_length=3, examples=["fresh"])


class IdeaEvidencePackIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_evidence_pack_id: str = Field(min_length=3, examples=["irep_001"])
    conversion_intent_id: str = Field(min_length=3, examples=["icnv_001"])
    candidate_id: str = Field(min_length=3, examples=["icand_001"])
    purpose: IdeaEvidenceIntakePurpose = Field(
        description="Report-side intake purpose for reviewed lotus-idea evidence."
    )
    evidence_packet_id: str = Field(min_length=3, examples=["ievp_001"])
    evidence_content_fingerprint: str = Field(
        pattern=r"^sha256:[a-zA-Z0-9_.:-]+$",
        description="Hash of the reviewed idea evidence packet; raw evidence is not accepted.",
        examples=["sha256:idea-evidence-content"],
    )
    source_signal_ids: tuple[str, ...] = Field(
        min_length=1,
        description="Source-safe signal identifiers from lotus-idea.",
        examples=[["sig_high_cash_001"]],
    )
    source_summaries: tuple[IdeaEvidenceSourceSummary, ...] = Field(
        min_length=1,
        description="Source-safe summaries of upstream product evidence.",
    )
    reason_codes: tuple[str, ...] = Field(
        min_length=1,
        description="Bounded reason codes explaining why the evidence pack was requested.",
        examples=[["HIGH_CASH_REVIEWED_FOR_REPORT"]],
    )
    report_source_authority: Literal["lotus-report"] = "lotus-report"
    render_source_authority: Literal["lotus-render"] = "lotus-render"
    archive_source_authority: Literal["lotus-archive"] = "lotus-archive"
    boundary: IdeaEvidenceIntakeBoundary = IdeaEvidenceIntakeBoundary.REPORT_INTAKE_ONLY
    retention_policy_ref: str = Field(min_length=3, examples=["generated-report-standard"])
    requested_at_utc: datetime
    grants_client_publication_authority: Literal[False] = False
    creates_rendered_output: Literal[False] = False
    creates_archive_record: Literal[False] = False
    producer: Literal["lotus-idea"] = "lotus-idea"
    supportability_status: Literal["not_certified"] = "not_certified"

    @field_validator("source_signal_ids", "reason_codes")
    @classmethod
    def _reject_empty_members(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("values must not be blank")
        return values


class IdeaEvidencePackIntakeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_id: str
    intake_status: Literal["accepted"]
    report_evidence_pack_id: str
    conversion_intent_id: str
    candidate_id: str
    producer: Literal["lotus-idea"]
    owned_product: Literal["lotus-report:ClientReportEvidencePack:v1"]
    supportability_status: Literal["not_certified"]
    route_existence_proven: Literal[True]
    materialization_proven: Literal[False]
    creates_report_job: Literal[False]
    creates_rendered_output: Literal[False]
    creates_archive_record: Literal[False]
    grants_client_publication_authority: Literal[False]
    remaining_blockers: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    accepted_at_utc: datetime
    correlation_id: str | None = None


class IdeaEvidencePackMaterializationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_evidence_pack: IdeaEvidencePackIntakeRequest
    portfolio_id: str = Field(
        min_length=3,
        description=(
            "Report-owned portfolio scope used to create the governed report job. This field is "
            "accepted only on the materialization route; the intake-only route remains "
            "source-safe and does not require portfolio identifiers."
        ),
        examples=["PB_SG_GLOBAL_BAL_001"],
    )
    mandate_id: str | None = Field(default=None, examples=["MANDATE_PB_SG_GLOBAL_BAL_001"])
    as_of_date: str = Field(min_length=4, examples=["2026-06-24"])
    requested_output_formats: list[str] = Field(
        default_factory=lambda: ["pdf"],
        description="Requested output formats for the generated report job.",
        examples=[["pdf"], ["json"]],
    )
    reporting_currency: str | None = Field(default=None, examples=["USD"])
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Output-affecting report options such as retention policy controls.",
        examples=[{"retention_policy_id": "generated-report-standard"}],
    )
    boundary: IdeaEvidenceMaterializationBoundary = (
        IdeaEvidenceMaterializationBoundary.REPORT_JOB_MATERIALIZATION
    )
    grants_client_publication_authority: Literal[False] = False
    producer: Literal["lotus-idea"] = "lotus-idea"
    supportability_status: Literal["not_certified"] = "not_certified"

    @field_validator("requested_output_formats")
    @classmethod
    def _reject_blank_output_formats(cls, values: list[str]) -> list[str]:
        if not values or any(not value.strip() for value in values):
            raise ValueError("requested_output_formats must not be blank")
        return values

    @field_validator("as_of_date")
    @classmethod
    def _validate_as_of_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("as_of_date must be an ISO calendar date") from exc
        return value


class IdeaEvidencePackMaterializationResponse(ReportJobHandleResponse):
    model_config = ConfigDict(extra="forbid")

    materialization_status: str = Field(
        description="Current report-owned materialization lifecycle status."
    )
    source_event_version: int = Field(
        gt=0,
        description=(
            "Positive Report-owned version derived from the append-only lifecycle event "
            "sequence for this materialization job. An unchanged value denotes exact owner "
            "replay; a larger value denotes newer Report state."
        ),
    )
    report_package_identity: IdeaEvidenceReportPackageIdentity
    producer: Literal["lotus-idea"] = "lotus-idea"
    source_authority: IdeaEvidenceMaterializationSourceAuthority = Field(
        default_factory=IdeaEvidenceMaterializationSourceAuthority
    )
    materialization_proven: Literal[True] = True
    creates_report_job: Literal[True] = True
    creates_rendered_output: bool
    creates_archive_record: bool
    grants_client_publication_authority: Literal[False] = False
    supported_feature_promoted: Literal[False] = False
    supportability_status: Literal["not_certified"] = "not_certified"
    remaining_blockers: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    render_job_id: str | None = None
    archive_document_id: str | None = None
