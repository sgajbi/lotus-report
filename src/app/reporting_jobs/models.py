from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReportJobStatus = Literal[
    "accepted",
    "queued",
    "collecting_data",
    "data_ready",
    "rendering",
    "completed",
    "archiving",
    "archived",
    "completed_with_warnings",
    "failed",
    "cancelled",
]

ReportFailureCategory = Literal[
    "entitlement_failed",
    "validation_failed",
    "upstream_data_failed",
    "data_incomplete",
    "render_validation_failed",
    "render_conflict",
    "render_execution_failed",
    "archive_validation_failed",
    "archive_conflict",
    "archive_storage_failed",
    "archive_execution_failed",
    "timeout",
    "cancelled",
    "operator_intervention_required",
]

ReportRerenderAttemptStatus = Literal[
    "rendering",
    "rendered",
    "archiving",
    "archived",
    "failed",
]

ReportRegenerateStatus = ReportJobStatus

ReportJobRelationshipType = Literal[
    "regenerate_replacement",
    "failed_work_replay",
    "batch_item_replay",
]


class ProposalNarrativeReviewPackage(BaseModel):
    model_config = ConfigDict(extra="allow")

    review_id: str | None = Field(
        default=None,
        description="lotus-advise narrative review identity that approved the package.",
        examples=["pnrev_001"],
    )
    review_state: str = Field(
        ...,
        description="Review state supplied by lotus-advise. Must be APPROVED_FOR_ADVISOR_USE.",
        examples=["APPROVED_FOR_ADVISOR_USE"],
    )
    reviewed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the narrative review decision was recorded.",
        examples=["2026-04-22T09:10:00Z"],
    )
    reviewed_by: str | None = Field(
        default=None,
        description="Actor or system principal that recorded the review decision.",
        examples=["advisor-123"],
    )


class ProposalNarrativeSectionPackage(BaseModel):
    model_config = ConfigDict(extra="allow")

    section_id: str = Field(
        ...,
        description="Stable advisory narrative section identity.",
        examples=["portfolio_context"],
    )
    title: str = Field(
        ...,
        description="Human-readable section title approved by lotus-advise.",
        examples=["Portfolio Context"],
    )
    body: str = Field(
        ...,
        description="Approved advisory narrative body text for report rendering.",
        examples=["The portfolio remains aligned to the balanced mandate."],
    )
    source_refs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Source references carried from lotus-advise for this section.",
    )


class ProposalNarrativeDisclosurePackage(BaseModel):
    model_config = ConfigDict(extra="allow")

    disclosure_id: str = Field(
        ...,
        description="Stable disclosure identity supplied by lotus-advise.",
        examples=["proposal_narrative.advisor_use_only.v1"],
    )
    text: str | None = Field(
        default=None,
        description="Disclosure text supplied by lotus-advise when available.",
        examples=["For advisor use only until the client-ready report workflow is approved."],
    )


class ProposalNarrativeReportPackage(BaseModel):
    model_config = ConfigDict(extra="allow")

    package_status: str = Field(
        ...,
        description=(
            "Package inclusion status. lotus-report accepts only INCLUDED_REVIEWED_NARRATIVE."
        ),
        examples=["INCLUDED_REVIEWED_NARRATIVE"],
    )
    usage: str = Field(
        ...,
        description="Intended usage boundary for the package supplied by lotus-advise.",
        examples=["REPORT_REQUEST_APPROVED_ADVISOR_NARRATIVE"],
    )
    proposal_id: str = Field(
        ...,
        description="Source proposal identity in lotus-advise.",
        examples=["prop_001"],
    )
    proposal_version_no: int = Field(
        ...,
        description="Source proposal version number approved for report inclusion.",
        examples=[3],
    )
    narrative_id: str = Field(
        ...,
        description="Source advisory narrative identity in lotus-advise.",
        examples=["pnar_001"],
    )
    narrative_status: str | None = Field(
        default=None,
        description="Source narrative lifecycle status supplied by lotus-advise.",
        examples=["APPROVED_FOR_ADVISOR_USE"],
    )
    generation_mode: str | None = Field(
        default=None,
        description="Narrative generation mode supplied by lotus-advise.",
        examples=["GOVERNED_AI_ASSISTED"],
    )
    audience: str | None = Field(
        default=None,
        description="Audience boundary for the approved narrative package.",
        examples=["advisor"],
    )
    policy_version: str | None = Field(
        default=None,
        description="lotus-advise narrative policy version used to approve the package.",
        examples=["proposal-narrative-policy.v1"],
    )
    review: ProposalNarrativeReviewPackage = Field(
        ...,
        description="Human or governed review decision that authorizes report inclusion.",
    )
    source_lineage: dict[str, Any] = Field(
        ...,
        description=(
            "Source lineage from lotus-advise, including source_narrative_hash and related "
            "proposal evidence hashes."
        ),
        examples=[{"source_narrative_hash": "sha256:narrative"}],
    )
    sections: list[ProposalNarrativeSectionPackage] = Field(
        default_factory=list,
        description="Approved narrative sections to render in report output.",
    )
    disclosures: list[ProposalNarrativeDisclosurePackage] = Field(
        default_factory=list,
        description="Disclosures that must travel with the approved narrative package.",
    )
    guardrail_results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Guardrail decisions supplied by lotus-advise.",
    )
    limitations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Known limitations supplied by lotus-advise for advisor/report consumption.",
    )
    ai_lineage: dict[str, Any] | None = Field(
        default=None,
        description="Optional AI lineage supplied by lotus-advise.",
    )
    execution_boundary: dict[str, Any] | None = Field(
        default=None,
        description="Execution and distribution boundary supplied by lotus-advise.",
    )

    @model_validator(mode="after")
    def validate_reviewed_package(self) -> "ProposalNarrativeReportPackage":
        if self.package_status != "INCLUDED_REVIEWED_NARRATIVE":
            raise ValueError(
                "proposal_narrative_package.package_status must be INCLUDED_REVIEWED_NARRATIVE"
            )
        if self.review.review_state != "APPROVED_FOR_ADVISOR_USE":
            raise ValueError(
                "proposal_narrative_package.review.review_state must be APPROVED_FOR_ADVISOR_USE"
            )
        source_hash = str(self.source_lineage.get("source_narrative_hash") or "").strip()
        if not source_hash:
            raise ValueError(
                "proposal_narrative_package.source_lineage.source_narrative_hash is required"
            )
        return self


class ProposalMemoReportPackage(BaseModel):
    model_config = ConfigDict(extra="allow")

    package_status: str = Field(
        ...,
        description="Memo package inclusion status supplied by lotus-advise.",
        examples=["INCLUDED_ADVISOR_PROPOSAL_MEMO"],
    )
    usage: str = Field(
        ...,
        description="Intended usage boundary for the memo package.",
        examples=["REPORT_REQUEST_APPROVED_ADVISOR_MEMO"],
    )
    memo_id: str = Field(..., description="Source memo identifier in lotus-advise.")
    memo_version: str = Field(..., description="Source memo package contract version.")
    memo_status: str = Field(..., description="Source memo evidence-pack readiness posture.")
    proposal_id: str = Field(..., description="Source proposal identifier in lotus-advise.")
    proposal_version_no: int = Field(..., description="Source proposal version number.")
    memo_hash: str = Field(..., description="SHA-256 hash of the memo package.")
    source_input_hash: str = Field(..., description="SHA-256 hash of memo source inputs.")
    review: dict[str, Any] = Field(
        default_factory=dict,
        description="Advisor-use review posture supplied by lotus-advise.",
    )
    projection: dict[str, Any] = Field(
        default_factory=dict,
        description="Memo projection posture supplied by lotus-advise.",
    )
    sections: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Advisor proposal memo sections authorized for report rendering.",
    )
    source_authority_manifest: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-authority readiness manifest supplied by lotus-advise.",
    )
    supportability: dict[str, Any] = Field(
        default_factory=dict,
        description="Memo supportability posture supplied by lotus-advise.",
    )
    client_ready_publication: str = Field(
        default="BLOCKED",
        description="Client-ready publication posture. Client-ready remains blocked.",
    )

    @model_validator(mode="after")
    def validate_memo_package(self) -> "ProposalMemoReportPackage":
        if self.package_status != "INCLUDED_ADVISOR_PROPOSAL_MEMO":
            raise ValueError(
                "proposal_memo_package.package_status must be INCLUDED_ADVISOR_PROPOSAL_MEMO"
            )
        if str(self.review.get("review_action") or "") != "APPROVE_FOR_ADVISOR_USE":
            raise ValueError(
                "proposal_memo_package.review.review_action must be APPROVE_FOR_ADVISOR_USE"
            )
        if self.client_ready_publication.upper() not in {"BLOCKED", "GATED"}:
            raise ValueError("client-ready memo publication is not supported")
        if not self.memo_hash.startswith("sha256:") or not self.source_input_hash.startswith(
            "sha256:"
        ):
            raise ValueError("proposal memo package hashes must use sha256 lineage")
        if not self.sections:
            raise ValueError("proposal_memo_package.sections is required")
        return self


class PortfolioReviewJobRequest(BaseModel):
    portfolio_scope: dict[str, Any] = Field(
        ...,
        description="Portfolio scope for the report job. First wave supports portfolio_ids.",
        examples=[{"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}],
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date for the report job.",
        examples=["2026-04-22"],
    )
    requested_output_formats: list[str] = Field(
        default_factory=lambda: ["json"],
        description=(
            "Requested output formats. The current wave supports JSON-only jobs that stop at "
            "`data_ready` and PDF jobs that submit a governed render package to lotus-render."
        ),
        examples=[["json"], ["pdf"]],
    )
    reporting_currency: str | None = Field(
        default=None,
        description="Optional reporting currency used in the report request hash.",
        examples=["USD"],
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Output-affecting report options included in idempotency hashing.",
        examples=[
            {
                "sections": ["OVERVIEW", "PERFORMANCE"],
                "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
            }
        ],
    )
    proposal_narrative_package: ProposalNarrativeReportPackage | None = Field(
        default=None,
        description=(
            "Optional approved proposal narrative package from lotus-advise. lotus-report "
            "preserves this source-authorized package in the immutable snapshot and render "
            "package; it does not approve, rewrite, or infer advisory narrative facts."
        ),
    )
    proposal_memo_package: ProposalMemoReportPackage | None = Field(
        default=None,
        description=(
            "Optional advisor-reviewed proposal memo package from lotus-advise. lotus-report "
            "preserves this source-authorized package in the immutable snapshot and render/archive "
            "handoff; it does not approve, rewrite, or infer memo facts."
        ),
    )


def _require_sha256(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("sha256:"):
        raise ValueError(f"{field_name} must use sha256 lineage")
    return normalized


class DpmSourceRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_system: str = Field(
        ...,
        min_length=1,
        description="Authoritative source system for the DPM handoff evidence.",
        examples=["lotus-manage"],
    )
    source_type: str | None = Field(
        default=None,
        description="Authoritative source contract type when supplied by lotus-manage.",
        examples=["DPM_PROOF_PACK_REPORT_INPUT"],
    )
    ref_type: str | None = Field(
        default=None,
        description="Alternative source contract type field used by some manage handoff payloads.",
        examples=["DPM_WAVE_REPORT_INPUT"],
    )
    source_id: str | None = Field(
        default=None,
        description="Stable source evidence identifier when supplied by lotus-manage.",
        examples=["dpp_001:dpm_proof_pack_report_input"],
    )
    ref_id: str | None = Field(
        default=None,
        description="Alternative stable source evidence identifier field.",
        examples=["dwv_001:dpm_wave_report_input"],
    )
    content_hash: str = Field(
        ...,
        description="SHA-256 content hash for the source evidence reference.",
        examples=["sha256:report-input"],
    )

    @model_validator(mode="after")
    def validate_source_ref(self) -> "DpmSourceRef":
        self.content_hash = _require_sha256(self.content_hash, "source_ref.content_hash")
        if not (self.source_type or self.ref_type):
            raise ValueError("source_ref source_type or ref_type is required")
        if not (self.source_id or self.ref_id):
            raise ValueError("source_ref source_id or ref_id is required")
        return self


class DpmProofPackSectionInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    section_id: str = Field(..., min_length=1)
    section_type: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    reason_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str = Field(..., description="SHA-256 section content hash.")

    @model_validator(mode="after")
    def validate_section_hash(self) -> "DpmProofPackSectionInput":
        self.content_hash = _require_sha256(
            self.content_hash,
            "proof_pack_report_input.sections.content_hash",
        )
        return self


class DpmOutcomeDimensionInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    dimension: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    reason_code: str | None = None
    expected: str | None = None
    realized: str | None = None
    variance: str | None = None
    explanation: str | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    supportability: dict[str, Any] = Field(default_factory=dict)


class DpmWaveItemInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    wave_item_id: str = Field(..., min_length=1)
    portfolio_id: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    proof_pack_id: str = Field(..., min_length=1)
    proof_pack_state: str = Field(..., min_length=1)
    reason_codes: list[str] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)


class DpmManagedReportInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    contract_version: str = Field(..., min_length=1)
    generated_at: datetime = Field(...)
    redaction_policy: str = Field(..., min_length=1)
    retention_policy: str = Field(
        ...,
        min_length=1,
        description="Source-owned retention posture for the DPM report input.",
        examples=["generated-report-standard"],
    )
    content_hash: str = Field(..., description="SHA-256 hash for the full DPM report input.")
    evidence_ref: DpmSourceRef = Field(
        ...,
        description="Stable source evidence reference for the manage-owned DPM report input.",
    )

    @model_validator(mode="after")
    def validate_managed_input_hash(self) -> "DpmManagedReportInput":
        self.content_hash = _require_sha256(self.content_hash, "content_hash")
        return self


class DpmOutcomeReportInput(DpmManagedReportInput):
    outcome_review_id: str = Field(..., min_length=1)
    outcome_review_content_hash: str = Field(
        ...,
        description="SHA-256 hash for the outcome-review source content.",
    )
    portfolio_id: str = Field(..., min_length=1)
    proof_pack_id: str = Field(..., min_length=1)
    review_window: dict[str, Any] = Field(...)
    state: str = Field(..., min_length=1)
    supportability: dict[str, Any] = Field(...)
    dimensions: list[DpmOutcomeDimensionInput] = Field(..., min_length=1)
    source_lineage: list[DpmSourceRef] = Field(..., min_length=1)
    source_hashes: dict[str, str] = Field(..., min_length=1)
    section_hashes: dict[str, str] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_outcome_input(self) -> "DpmOutcomeReportInput":
        self.outcome_review_content_hash = _require_sha256(
            self.outcome_review_content_hash,
            "outcome_report_input.outcome_review_content_hash",
        )
        for key, value in self.source_hashes.items():
            self.source_hashes[key] = _require_sha256(value, "outcome_report_input.source_hashes")
        for key, value in self.section_hashes.items():
            self.section_hashes[key] = _require_sha256(value, "outcome_report_input.section_hashes")
        review_end = self.review_window.get("end_date") or self.review_window.get("period_end")
        if not review_end:
            raise ValueError("outcome_report_input review window end date is required")
        if not _supportability_state(self.supportability):
            raise ValueError("outcome_report_input.supportability state is required")
        return self


class DpmProofPackReportInput(DpmManagedReportInput):
    proof_pack_id: str = Field(..., min_length=1)
    proof_pack_content_hash: str = Field(
        ...,
        description="SHA-256 hash for the proof-pack source content.",
    )
    portfolio_id: str = Field(..., min_length=1)
    as_of_date: date = Field(...)
    state: str = Field(..., min_length=1)
    supportability: dict[str, Any] = Field(...)
    sections: list[DpmProofPackSectionInput] = Field(..., min_length=1)
    source_hashes: dict[str, str] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_proof_pack_input(self) -> "DpmProofPackReportInput":
        self.proof_pack_content_hash = _require_sha256(
            self.proof_pack_content_hash,
            "proof_pack_report_input.proof_pack_content_hash",
        )
        for key, value in self.source_hashes.items():
            self.source_hashes[key] = _require_sha256(
                value,
                "proof_pack_report_input.source_hashes",
            )
        if not _supportability_state(self.supportability):
            raise ValueError("proof_pack_report_input.supportability state is required")
        return self


class DpmWaveReportInput(DpmManagedReportInput):
    wave_id: str = Field(..., min_length=1)
    wave_content_hash: str = Field(..., description="SHA-256 hash for the wave source content.")
    wave_state: str = Field(..., min_length=1)
    trigger_type: str = Field(..., min_length=1)
    trigger_id: str = Field(..., min_length=1)
    as_of_date: date = Field(...)
    supportability: dict[str, Any] = Field(...)
    proof_pack_posture: dict[str, Any] = Field(...)
    items: list[DpmWaveItemInput] = Field(..., min_length=1)
    source_refs: list[DpmSourceRef] = Field(..., min_length=1)
    external_execution_claimed: bool = False

    @model_validator(mode="after")
    def validate_wave_input(self) -> "DpmWaveReportInput":
        self.wave_content_hash = _require_sha256(
            self.wave_content_hash,
            "wave_report_input.wave_content_hash",
        )
        if not _supportability_state(self.supportability):
            raise ValueError("wave_report_input.supportability state is required")
        return self


def _supportability_state(value: dict[str, Any]) -> str | None:
    for key in ("status", "state", "supportability_state"):
        candidate = str(value.get(key) or "").strip()
        if candidate:
            return candidate
    return None


class OutcomeReviewReportJobRequest(BaseModel):
    outcome_report_input: DpmOutcomeReportInput = Field(
        ...,
        description=(
            "Manage-owned DpmOutcomeReportInput payload. lotus-report treats this as bounded "
            "source truth for outcome-review report generation and never recomputes outcome facts."
        ),
        examples=[
            {
                "contract_version": "1.0",
                "outcome_review_id": "dor_001",
                "outcome_review_content_hash": "sha256:outcome-review",
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "proof_pack_id": "dpp_001",
                "review_window": {
                    "start_date": "2026-04-22",
                    "end_date": "2026-04-23",
                },
                "generated_at": "2026-04-23T09:00:00Z",
                "report_title": "Post-Trade Outcome Review - PB_SG_GLOBAL_BAL_001",
                "report_audience": ["portfolio_manager", "cio_office", "audit"],
                "state": "READY",
                "overall_outcome": "Execution outcome aligned with pre-trade proof.",
                "dimensions": [],
                "source_lineage": [],
                "source_hashes": {"realized": "sha256:realized"},
                "section_hashes": {"proof_pack": "sha256:proof-pack"},
                "redaction_policy": "NO_RAW_PAYLOADS",
                "retention_policy": "generated-report-standard",
                "evidence_ref": {
                    "source_system": "lotus-manage",
                    "source_type": "DPM_OUTCOME_REPORT_INPUT",
                    "source_id": "dor_001:dpm_outcome_report_input",
                    "content_hash": "sha256:report-input",
                },
                "content_hash": "sha256:report-input",
            }
        ],
    )
    requested_output_formats: list[str] = Field(
        default_factory=lambda: ["pdf"],
        description=(
            "Requested output formats. Outcome-review report jobs are intended for governed PDF "
            "artifact generation; JSON-only jobs may be used for snapshot certification."
        ),
        examples=[["pdf"], ["json"]],
    )
    reporting_currency: str | None = Field(
        default=None,
        description="Optional reporting currency used for request hashing and render context.",
        examples=["USD"],
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Output-affecting report options such as retention policy or template controls."
        ),
        examples=[{"retention_policy_id": "generated-report-standard"}],
    )


class ProofPackReportJobRequest(BaseModel):
    proof_pack_report_input: DpmProofPackReportInput = Field(
        ...,
        description=(
            "Manage-owned DpmProofPackReportInput payload. lotus-report treats this as bounded "
            "source truth for pre-trade proof-pack report generation and never recomputes "
            "proof-pack facts."
        ),
        examples=[
            {
                "contract_version": "1.0",
                "proof_pack_id": "dpp_001",
                "proof_pack_content_hash": "sha256:proof-pack",
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "mandate_id": "MANDATE_PB_SG_GLOBAL_BAL_001",
                "as_of_date": "2026-05-03",
                "generated_at": "2026-05-03T09:00:00Z",
                "report_title": "Pre-Trade Proof Pack - PB_SG_GLOBAL_BAL_001",
                "report_audience": ["portfolio_manager", "investment_control", "audit"],
                "decision_summary": {
                    "recommended_action": "approve_rebalance",
                    "rationale": "Mandate drift and source readiness support rebalance approval.",
                },
                "supportability": {"status": "READY", "reason_codes": ["proof_pack_ready"]},
                "sections": [
                    {
                        "section_id": "sec_mandate",
                        "section_type": "MANDATE_CONTEXT",
                        "state": "READY",
                        "title": "Mandate context",
                        "summary": "Mandate, model, and policy evidence are aligned.",
                        "reason_codes": ["mandate_context_ready"],
                        "facts": {},
                        "metrics": {},
                        "evidence_refs": [],
                        "source_refs": [],
                        "content_hash": "sha256:section-mandate",
                    }
                ],
                "markdown_summary": "# Pre-Trade Proof Pack",
                "source_hashes": {"mandate": "sha256:mandate"},
                "redaction_policy": "NO_RAW_PAYLOADS",
                "retention_policy": "generated-report-standard",
                "evidence_ref": {
                    "source_system": "lotus-manage",
                    "source_type": "DPM_PROOF_PACK_REPORT_INPUT",
                    "source_id": "dpp_001:dpm_proof_pack_report_input",
                    "content_hash": "sha256:report-input",
                },
                "content_hash": "sha256:report-input",
            }
        ],
    )
    requested_output_formats: list[str] = Field(
        default_factory=lambda: ["pdf"],
        description=(
            "Requested output formats. Proof-pack report jobs are intended for governed PDF "
            "artifact generation; JSON-only jobs may be used for snapshot certification."
        ),
        examples=[["pdf"], ["json"]],
    )
    reporting_currency: str | None = Field(
        default=None,
        description="Optional reporting currency used for request hashing and render context.",
        examples=["USD"],
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Output-affecting report options such as retention policy or template controls."
        ),
        examples=[{"retention_policy_id": "generated-report-standard"}],
    )


class WaveReportJobRequest(BaseModel):
    wave_report_input: DpmWaveReportInput = Field(
        ...,
        description=(
            "Manage-owned DpmWaveReportInput payload. lotus-report treats this as bounded source "
            "truth for rebalance-wave report generation and never recomputes wave state, "
            "proof-pack linkage, supportability, or handoff facts."
        ),
        examples=[
            {
                "contract_version": "1.0",
                "wave_id": "dwv_001",
                "wave_content_hash": "sha256:wave",
                "wave_state": "HANDOFF_READY",
                "trigger_type": "EXPLICIT_PORTFOLIO_LIST",
                "trigger_id": "manual-wave-001",
                "trigger_rationale": "Review explicit affected portfolio list.",
                "as_of_date": "2026-05-03",
                "generated_at": "2026-05-03T09:00:00Z",
                "report_title": "Rebalance Wave Evidence - dwv_001",
                "report_audience": ["portfolio_manager", "operations", "audit"],
                "aggregate_metrics": {
                    "item_count": 1,
                    "state_counts": {"HANDOFF_READY": 1},
                    "ready_item_count": 1,
                    "blocked_item_count": 0,
                },
                "supportability": {
                    "supportability_state": "ready",
                    "reason": "wave_supportability_ready",
                },
                "proof_pack_posture": {
                    "linked_item_count": 1,
                    "ready_proof_pack_count": 1,
                    "degraded_proof_pack_count": 0,
                },
                "items": [
                    {
                        "wave_item_id": "dwi_001",
                        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                        "mandate_id": "MANDATE_PB_SG_GLOBAL_BAL_001",
                        "model_portfolio_id": "MODEL_PB_SG_GLOBAL_BAL_DPM",
                        "state": "HANDOFF_READY",
                        "reason_codes": ["WAVE_ITEM_HANDOFF_READY"],
                        "selected_alternative_id": "alt_min_turnover",
                        "proof_pack_id": "dpp_001",
                        "proof_pack_state": "READY",
                        "source_refs": [],
                        "diagnostics": {"external_execution_claimed": False},
                    }
                ],
                "events": [],
                "handoff_refs": [],
                "source_refs": [
                    {
                        "source_system": "lotus-manage",
                        "source_type": "DPM_WAVE_REPORT_INPUT",
                        "source_id": "dwv_001:dpm_wave_report_input",
                        "content_hash": "sha256:report-input",
                    }
                ],
                "redaction_policy": "NO_RAW_PAYLOADS",
                "retention_policy": "generated-report-standard",
                "external_execution_claimed": False,
                "evidence_ref": {
                    "source_system": "lotus-manage",
                    "ref_type": "DPM_WAVE_REPORT_INPUT",
                    "ref_id": "dwv_001:dpm_wave_report_input",
                    "content_hash": "sha256:report-input",
                },
                "content_hash": "sha256:report-input",
            }
        ],
    )
    requested_output_formats: list[str] = Field(
        default_factory=lambda: ["pdf"],
        description=(
            "Requested output formats. Wave report jobs are intended for governed PDF artifact "
            "generation; JSON-only jobs may be used for snapshot certification."
        ),
        examples=[["pdf"], ["json"]],
    )
    reporting_currency: str | None = Field(
        default=None,
        description="Optional reporting currency used for request hashing and render context.",
        examples=["USD"],
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Output-affecting report options such as retention policy or template controls."
        ),
        examples=[{"retention_policy_id": "generated-report-standard"}],
    )


PORTFOLIO_REVIEW_JOB_REQUEST_EXAMPLE: dict[str, Any] = {
    "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
    "as_of_date": "2026-04-22",
    "requested_output_formats": ["pdf"],
    "reporting_currency": "USD",
    "options": {
        "sections": ["OVERVIEW", "PERFORMANCE", "RISK_ANALYTICS"],
        "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
    },
    "proposal_narrative_package": {
        "package_status": "INCLUDED_REVIEWED_NARRATIVE",
        "usage": "REPORT_REQUEST_APPROVED_ADVISOR_NARRATIVE",
        "proposal_id": "prop_001",
        "proposal_version_no": 3,
        "narrative_id": "pnar_001",
        "narrative_status": "APPROVED_FOR_ADVISOR_USE",
        "generation_mode": "GOVERNED_AI_ASSISTED",
        "audience": "advisor",
        "policy_version": "proposal-narrative-policy.v1",
        "review": {
            "review_id": "pnrev_001",
            "review_state": "APPROVED_FOR_ADVISOR_USE",
            "reviewed_at": "2026-04-22T09:10:00Z",
            "reviewed_by": "advisor-123",
        },
        "source_lineage": {
            "source_narrative_hash": "sha256:narrative",
            "proposal_hash": "sha256:proposal",
            "proposal_version_hash": "sha256:proposal-version",
        },
        "sections": [
            {
                "section_id": "portfolio_context",
                "title": "Portfolio Context",
                "body": "The portfolio remains aligned to the balanced mandate.",
                "source_refs": [{"source_system": "lotus-advise", "source_id": "prop_001"}],
            }
        ],
        "disclosures": [
            {
                "disclosure_id": "proposal_narrative.advisor_use_only.v1",
                "text": "For advisor use only until the client-ready workflow is approved.",
            }
        ],
        "guardrail_results": [{"guardrail_id": "no_trade_instruction", "status": "passed"}],
        "limitations": [{"limitation_id": "advisor_use_only", "status": "active"}],
        "execution_boundary": {"client_distribution_allowed": False},
    },
}

OUTCOME_REVIEW_REPORT_JOB_REQUEST_EXAMPLE: dict[str, Any] = {
    "outcome_report_input": OutcomeReviewReportJobRequest.model_fields[
        "outcome_report_input"
    ].examples[0],
    "requested_output_formats": ["pdf"],
    "reporting_currency": "USD",
    "options": {"retention_policy_id": "generated-report-standard"},
}

PROOF_PACK_REPORT_JOB_REQUEST_EXAMPLE: dict[str, Any] = {
    "proof_pack_report_input": ProofPackReportJobRequest.model_fields[
        "proof_pack_report_input"
    ].examples[0],
    "requested_output_formats": ["pdf"],
    "reporting_currency": "USD",
    "options": {"retention_policy_id": "generated-report-standard"},
}

WAVE_REPORT_JOB_REQUEST_EXAMPLE: dict[str, Any] = {
    "wave_report_input": WaveReportJobRequest.model_fields["wave_report_input"].examples[0],
    "requested_output_formats": ["pdf"],
    "reporting_currency": "USD",
    "options": {"retention_policy_id": "generated-report-standard"},
}


REPORT_JOB_HANDLE_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_request_id": "rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "status": "archived",
    "status_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5",
    "idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
}


REPORT_JOB_STATUS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "report_request_id": "rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
    "report_type": "portfolio_review",
    "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
    "status": "archived",
    "failure_category": None,
    "failure_message": None,
    "current_step": "archived",
    "retry_eligible": False,
    "cancel_requested": False,
    "created_at": "2026-04-22T09:00:00Z",
    "updated_at": "2026-04-22T09:00:03Z",
    "started_at": None,
    "completed_at": "2026-04-22T09:00:03Z",
    "cancelled_at": None,
    "correlation_id": "corr-portfolio-review-1",
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    "render": {
        "render_job_id": "rdr_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
        "output_format": "pdf",
        "template_id": "portfolio-review",
        "template_version": "v1",
        "artifact_sha256": "sha256:artifact-portfolio-review",
        "bounded_determinism_fingerprint": "typst-0.14.2:a4c71e5d",
        "runtime_engine": "typst",
        "runtime_engine_version": "0.14.2",
        "render_duration_ms": 812,
    },
    "archive": {
        "archive_request_id": "arch_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
        "document_id": "doc_83ca965c50334c40a17d2b8cc94873a5",
        "completed_at": "2026-04-22T09:00:04Z",
    },
}


REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "events": [
        {
            "status_event_id": "rse_d7e9c3b87d864b098997d4fe5bd2de2a",
            "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "from_status": None,
            "to_status": "accepted",
            "event_type": "job_accepted",
            "event_schema_version": "report-status-event.v1",
            "event_family": "job_lifecycle",
            "event_payload": {
                "event_type": "job_accepted",
                "from_status": None,
                "to_status": "accepted",
                "report_type": "portfolio_review",
            },
            "event_idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
            "message": "Portfolio review report job accepted.",
            "actor": "advisor-123",
            "created_at": "2026-04-22T09:00:00Z",
            "correlation_id": "corr-portfolio-review-1",
            "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        }
    ],
}

REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "status": REPORT_JOB_STATUS_RESPONSE_EXAMPLE,
    "event_count": 4,
    "latest_event": {
        **REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE["events"][0],
        "to_status": "archived",
        "event_type": "job_archived",
        "message": "Report artifact archived by lotus-archive.",
    },
    "snapshot": {
        "snapshot_id": "rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf",
        "snapshot_hash": "sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd",
        "supportability_status": "complete",
        "completeness_status": "complete",
        "captured_at": "2026-04-22T09:00:03Z",
    },
    "lineage": {
        "upstream_call_count": 3,
        "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
        "supportability_status": "complete",
        "completeness_status": "complete",
        "failure_categories": [],
    },
    "relationships": [
        {
            "relationship_id": "rjr_3e6d73f12e344448bc9a6607959dfb6a",
            "relationship_type": "regenerate_replacement",
            "source_report_job_id": "rjob_source_001",
            "derived_report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "source_status": "archived",
            "derived_status": "archived",
            "source_failure_category": None,
            "derived_failure_category": None,
            "archive_consequence": "replacement",
            "previous_archive_document_id": "doc_previous",
            "new_archive_document_id": "doc_replacement",
            "actor": "advisor-123",
            "reason": "Certified upstream position correction.",
            "created_at": "2026-04-22T09:02:00Z",
            "updated_at": "2026-04-22T09:02:10Z",
        }
    ],
    "rerender_attempts": [
        {
            "rerender_attempt_id": "rrnd_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
            "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "status": "archived",
            "snapshot_id": "rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf",
            "snapshot_hash": (
                "sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"
            ),
            "previous_render_job_id": "rdr_original_pdf",
            "previous_archive_document_id": "doc_previous",
            "archive_consequence": "correction",
            "failure_category": None,
            "failure_message": None,
            "retry_eligible": False,
            "render": {
                "render_job_id": "rdr_rrnd_4f7c85b39f7d4e7b8d0bb420d34a1d2c_pdf",
                "output_format": "pdf",
                "template_id": "portfolio-review",
                "template_version": "v1",
                "artifact_sha256": "sha256:correction-artifact",
                "bounded_determinism_fingerprint": "typst-0.14.2:7b2d31f1",
                "runtime_engine": "typst",
                "runtime_engine_version": "0.14.2",
                "render_duration_ms": 731,
            },
            "archive": {
                "archive_request_id": "arch_rrnd_4f7c85b39f7d4e7b8d0bb420d34a1d2c_pdf",
                "document_id": "doc_correction",
                "completed_at": "2026-04-22T09:04:04Z",
            },
            "created_at": "2026-04-22T09:04:00Z",
            "updated_at": "2026-04-22T09:04:04Z",
        }
    ],
    "render": REPORT_JOB_STATUS_RESPONSE_EXAMPLE["render"],
    "archive": REPORT_JOB_STATUS_RESPONSE_EXAMPLE["archive"],
    "diagnostic_flags": [],
    "operation_links": {
        "status_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5",
        "events_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/events",
        "snapshot_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/snapshot",
        "lineage_url": "/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/lineage",
    },
}

REPORT_PORTFOLIO_MEMORY_EVENTS_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
    "report_type": "proof_pack",
    "event_count": 3,
    "supportability_state": "READY",
    "source_systems": ["lotus-report"],
    "reason_codes": ["REPORT_EVENT_FAMILY_READY"],
    "governance_policy": {
        "event_identity_scheme": (
            "source_system:source_type:source_id:content_hash_or_content_hash_unavailable"
        ),
        "retention_policy": "DPM_PORTFOLIO_MEMORY_SOURCE_LINEAGE_7Y",
        "redaction_policy": "NO_RAW_PAYLOADS",
        "audit_policy": "AUDIT_READ_AND_EXPORT",
        "access_classification": "CLIENT_CONFIDENTIAL_INTERNAL",
    },
    "content_hash": ("sha256:6a8dfe91ed58dce965f7825713c8bf0f2b669a50bf198b3e1fb7be6474b53e2e"),
    "generated_at": "2026-04-22T09:00:04Z",
    "events": [
        {
            "event_id": (
                "report-memory:rjob_83ca965c50334c40a17d2b8cc94873a5:"
                "rse_d7e9c3b87d864b098997d4fe5bd2de2a"
            ),
            "event_identity": (
                "lotus-report:REPORT_STATUS_EVENT:"
                "rse_d7e9c3b87d864b098997d4fe5bd2de2a:"
                "sha256:7ce6f9c6c5385fca0c5751f3446d575d00e27d467131ba42e0fae019ca27db21"
            ),
            "event_type": "REPORT_JOB_ACCEPTED",
            "event_time": "2026-04-22T09:00:00Z",
            "actor": "advisor-123",
            "source_system": "lotus-report",
            "source_type": "REPORT_STATUS_EVENT",
            "source_id": "rse_d7e9c3b87d864b098997d4fe5bd2de2a",
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "report_type": "proof_pack",
            "status": "accepted",
            "supportability_state": "PENDING_REVIEW",
            "summary": "Report job accepted for proof_pack.",
            "reason_codes": ["REPORT_JOB_ACCEPTED"],
            "source_refs": [
                {
                    "source_system": "lotus-report",
                    "source_type": "REPORT_JOB",
                    "source_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
                    "content_hash": "sha256:request-hash",
                },
                {
                    "source_system": "lotus-report",
                    "source_type": "REPORT_STATUS_EVENT",
                    "source_id": "rse_d7e9c3b87d864b098997d4fe5bd2de2a",
                    "content_hash": (
                        "sha256:7ce6f9c6c5385fca0c5751f3446d575d00e27d467131ba42e0fae019ca27db21"
                    ),
                },
            ],
            "artifact_refs": [],
            "content_hash": (
                "sha256:7ce6f9c6c5385fca0c5751f3446d575d00e27d467131ba42e0fae019ca27db21"
            ),
            "metadata": {
                "correlation_id": "corr-portfolio-review-1",
                "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
            },
        }
    ],
}

REPORT_JOB_LIST_FILTERS_EXAMPLE: dict[str, Any] = {
    "tenant_id": "tenant-sg",
    "region": "APAC",
    "status": "archived",
    "report_type": "portfolio_review",
    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
    "as_of_date": "2026-04-22",
    "idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
    "correlation_id": "corr-portfolio-review-1",
    "created_from": "2026-04-22T00:00:00Z",
    "created_to": "2026-04-23T00:00:00Z",
    "limit": 25,
}

REPORT_JOB_LIST_RESPONSE_EXAMPLE: dict[str, Any] = {
    "count": 1,
    "applied_filters": REPORT_JOB_LIST_FILTERS_EXAMPLE,
    "items": [
        {
            "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
            "report_request_id": "rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
            "report_type": "portfolio_review",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
            "as_of_date": "2026-04-22",
            "status": "archived",
            "failure_category": None,
            "current_step": "archived",
            "retry_eligible": False,
            "cancel_requested": False,
            "idempotency_key": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
            "correlation_id": "corr-portfolio-review-1",
            "created_at": "2026-04-22T09:00:00Z",
            "updated_at": "2026-04-22T09:00:03Z",
            "render": {
                "render_job_id": "rdr_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
                "output_format": "pdf",
                "template_id": "portfolio-review",
                "template_version": "v1",
                "artifact_sha256": "sha256:artifact-portfolio-review",
                "bounded_determinism_fingerprint": "typst-0.14.2:a4c71e5d",
                "runtime_engine": "typst",
                "runtime_engine_version": "0.14.2",
                "render_duration_ms": 812,
            },
            "archive": {
                "archive_request_id": "arch_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
                "document_id": "doc_83ca965c50334c40a17d2b8cc94873a5",
                "completed_at": "2026-04-22T09:00:04Z",
            },
        }
    ],
}

API_ERROR_RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "missing_idempotency_key": {
        "detail": {
            "code": "missing_idempotency_key",
            "message": "Idempotency-Key is required.",
        }
    },
    "missing_caller_context": {
        "detail": {
            "code": "missing_caller_context",
            "message": "Required caller context headers are missing.",
            "missing_headers": [
                "X-Actor-Id",
                "X-Caller-Application",
                "X-Tenant-Id",
                "X-Region",
            ],
        }
    },
    "idempotency_conflict": {
        "detail": {
            "code": "idempotency_conflict",
            "message": "Idempotency-Key was reused with a different report request.",
        }
    },
    "report_job_not_found": {
        "detail": {
            "code": "report_job_not_found",
            "message": "Report job was not found.",
        }
    },
    "report_snapshot_not_found": {
        "detail": {
            "code": "report_snapshot_not_found",
            "message": "Report snapshot was not found.",
        }
    },
    "report_lineage_store_unavailable": {
        "detail": {
            "code": "report_lineage_store_unavailable",
            "message": "Report lineage diagnostics are temporarily unavailable.",
        }
    },
    "report_job_cannot_be_rerendered": {
        "detail": {
            "code": "report_job_cannot_be_rerendered",
            "message": "Report job is not eligible for rerender from snapshot.",
        }
    },
    "report_job_cannot_be_regenerated": {
        "detail": {
            "code": "report_job_cannot_be_regenerated",
            "message": "Report job is not eligible for regeneration from upstream data.",
        }
    },
    "report_job_cannot_be_replayed": {
        "detail": {
            "code": "report_job_cannot_be_replayed",
            "message": "Report job is not eligible for replay.",
        }
    },
    "report_job_cannot_be_cancelled": {
        "detail": {
            "code": "report_job_cannot_be_cancelled",
            "message": "Report job can no longer be cancelled.",
        }
    },
    "invalid_report_job_filters": {
        "detail": {
            "code": "invalid_report_job_filters",
            "message": "At least one supported job-search filter is required.",
        }
    },
}

REPORT_JOB_RERENDER_RESPONSE_EXAMPLE: dict[str, Any] = {
    "report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "rerender_attempt_id": "rrnd_4f7c85b39f7d4e7b8d0bb420d34a1d2c",
    "idempotency_key": "rerender-rjob_83ca965c50334c40a17d2b8cc94873a5-template-fix-1",
    "status": "archived",
    "snapshot_id": "rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf",
    "snapshot_hash": "sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd",
    "previous_render_job_id": "rdr_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf",
    "previous_archive_document_id": "doc_83ca965c50334c40a17d2b8cc94873a5",
    "archive_consequence": "correction",
    "failure_category": None,
    "failure_message": None,
    "retry_eligible": False,
    "render": {
        "render_job_id": "rdr_rrnd_4f7c85b39f7d4e7b8d0bb420d34a1d2c_pdf",
        "output_format": "pdf",
        "template_id": "portfolio-review",
        "template_version": "v1",
        "artifact_sha256": "sha256:artifact-portfolio-review-rerender",
        "bounded_determinism_fingerprint": "typst-0.14.2:b8e42bb1",
        "runtime_engine": "typst",
        "runtime_engine_version": "0.14.2",
        "render_duration_ms": 731,
    },
    "archive": {
        "archive_request_id": "arch_rdr_rrnd_4f7c85b39f7d4e7b8d0bb420d34a1d2c_pdf",
        "document_id": "doc_correction_83ca965c50334c40a17d2b8cc94873a5",
        "completed_at": "2026-04-22T09:07:04Z",
    },
    "created_at": "2026-04-22T09:07:00Z",
    "updated_at": "2026-04-22T09:07:04Z",
}

REPORT_JOB_REGENERATE_RESPONSE_EXAMPLE: dict[str, Any] = {
    "source_report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "regenerated_report_job_id": "rjob_5ce4b4a63bb84bb68e7dc190fdf6a3cd",
    "idempotency_key": "regenerate-rjob_83ca965c50334c40a17d2b8cc94873a5-refresh-1",
    "status": "archived",
    "previous_snapshot_id": "rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf",
    "new_snapshot_id": "rsnap_b98d1c76ad9d47da880f0863df2d3f83",
    "previous_snapshot_hash": (
        "sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"
    ),
    "new_snapshot_hash": (
        "sha256:bdbb727e97934f629f8a2ed2fb88cf9e42b6cfc06c6615a378d3f9a1d9f77811"
    ),
    "previous_archive_document_id": "doc_83ca965c50334c40a17d2b8cc94873a5",
    "new_archive_document_id": "doc_replacement_83ca965c50334c40a17d2b8cc94873a5",
    "archive_consequence": "replacement",
    "failure_category": None,
    "failure_message": None,
    "retry_eligible": False,
    "render": {
        "render_job_id": "rdr_rjob_5ce4b4a63bb84bb68e7dc190fdf6a3cd_pdf",
        "output_format": "pdf",
        "template_id": "portfolio-review",
        "template_version": "v1",
        "artifact_sha256": "sha256:artifact-portfolio-review-regenerate",
        "bounded_determinism_fingerprint": "typst-0.14.2:9dd87c01",
        "runtime_engine": "typst",
        "runtime_engine_version": "0.14.2",
        "render_duration_ms": 804,
    },
    "archive": {
        "archive_request_id": "arch_rdr_rjob_5ce4b4a63bb84bb68e7dc190fdf6a3cd_pdf",
        "document_id": "doc_replacement_83ca965c50334c40a17d2b8cc94873a5",
        "completed_at": "2026-04-22T09:12:04Z",
    },
    "created_at": "2026-04-22T09:12:00Z",
    "updated_at": "2026-04-22T09:12:04Z",
}

REPORT_JOB_REPLAY_RESPONSE_EXAMPLE: dict[str, Any] = {
    "source_report_job_id": "rjob_83ca965c50334c40a17d2b8cc94873a5",
    "replayed_report_job_id": "rjob_8a1f4d267de64a5597c02cfeb612a6a4",
    "idempotency_key": "replay-rjob_83ca965c50334c40a17d2b8cc94873a5-upstream-retry-1",
    "status": "archived",
    "source_failure_category": "upstream_data_failed",
    "failure_category": None,
    "failure_message": None,
    "retry_eligible": False,
    "render": {
        "render_job_id": "rdr_rjob_8a1f4d267de64a5597c02cfeb612a6a4_pdf",
        "output_format": "pdf",
        "template_id": "portfolio-review",
        "template_version": "v1",
        "artifact_sha256": "sha256:artifact-portfolio-review-replay",
        "bounded_determinism_fingerprint": "typst-0.14.2:129fb9da",
        "runtime_engine": "typst",
        "runtime_engine_version": "0.14.2",
        "render_duration_ms": 782,
    },
    "archive": {
        "archive_request_id": "arch_rdr_rjob_8a1f4d267de64a5597c02cfeb612a6a4_pdf",
        "document_id": "doc_replay_8a1f4d267de64a5597c02cfeb612a6a4",
        "completed_at": "2026-04-22T09:18:04Z",
    },
    "created_at": "2026-04-22T09:18:00Z",
    "updated_at": "2026-04-22T09:18:04Z",
}


class ReportCallerContext(BaseModel):
    trigger_type: Literal["user", "system"] = Field(
        "user",
        description="Origin category for the request: user-driven or system-driven.",
        examples=["user"],
    )
    triggered_by: str = Field(
        ...,
        description="Authenticated actor or system principal that triggered the request.",
        examples=["advisor-123"],
    )
    caller_application: str = Field(
        ...,
        description="Calling Lotus application at the governed boundary.",
        examples=["lotus-gateway"],
    )
    tenant_id: str = Field(
        ...,
        description="Tenant identifier for entitlement and audit.",
        examples=["tenant-sg"],
    )
    region: str = Field(
        ...,
        description="Operating region for segregation and audit.",
        examples=["APAC"],
    )
    booking_center_code: str | None = Field(
        default=None,
        description="Optional booking center associated with the caller or request.",
        examples=["SG"],
    )
    role: str | None = Field(
        default=None,
        description="Optional caller role captured for audit and support diagnostics.",
        examples=["advisor"],
    )
    correlation_id: str = Field(
        ...,
        description="End-to-end correlation identifier propagated through Lotus services.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier propagated through Lotus services.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ApiErrorDetail(BaseModel):
    code: str = Field(
        ...,
        description="Machine-readable error code for deterministic client handling.",
        examples=["report_job_not_found"],
    )
    message: str = Field(
        ...,
        description="Support-safe error message explaining the failure.",
        examples=["Report job was not found."],
    )
    missing_headers: list[str] | None = Field(
        default=None,
        description="Header names that must be supplied when caller context is incomplete.",
        examples=[["X-Actor-Id", "X-Tenant-Id", "X-Region"]],
    )


class ApiErrorResponse(BaseModel):
    detail: ApiErrorDetail = Field(
        ...,
        description="Structured API error payload for product and operator consumers.",
        examples=[API_ERROR_RESPONSE_EXAMPLES["report_job_not_found"]["detail"]],
    )


class ReportJobHandleResponse(BaseModel):
    report_request_id: str = Field(
        ...,
        description="Opaque durable report request identifier stored in the ledger.",
        examples=["rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c"],
    )
    report_job_id: str = Field(
        ...,
        description="Opaque durable report job identifier used for status and cancellation.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    status: ReportJobStatus = Field(
        ...,
        description="Current product-safe job status.",
        examples=["accepted"],
    )
    status_url: str = Field(
        ...,
        description="Relative URL where callers can retrieve product-safe job status.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key associated with this request.",
        examples=["portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22"],
    )


class ReportJobRenderInfo(BaseModel):
    render_job_id: str | None = Field(
        default=None,
        description="Opaque lotus-render job identifier when rendering was requested.",
        examples=["rdr_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf"],
    )
    output_format: str | None = Field(
        default=None,
        description="Rendered output format when rendering was requested.",
        examples=["pdf"],
    )
    template_id: str | None = Field(
        default=None,
        description="Template identifier used by lotus-render.",
        examples=["portfolio-review"],
    )
    template_version: str | None = Field(
        default=None,
        description="Template version used by lotus-render.",
        examples=["v1"],
    )
    artifact_sha256: str | None = Field(
        default=None,
        description="Rendered artifact hash when rendering completed successfully.",
        examples=["sha256:2f817e5d665db6c709e1a9f2332ff7fa609d7304c55ba921f97d9b2d71b0679d"],
    )
    bounded_determinism_fingerprint: str | None = Field(
        default=None,
        description="Bounded determinism fingerprint when rendering completed successfully.",
        examples=["376a56c2eae1ccd6a1e09f8c51b190d098b7b7221e266c86dcc524132b745140"],
    )
    runtime_engine: str | None = Field(
        default=None,
        description="Render engine reported by lotus-render.",
        examples=["typst"],
    )
    runtime_engine_version: str | None = Field(
        default=None,
        description="Render engine version reported by lotus-render.",
        examples=["0.14.2"],
    )
    render_duration_ms: int | None = Field(
        default=None,
        description="Measured render duration in milliseconds when available.",
        examples=[842],
    )


class ReportJobArchiveInfo(BaseModel):
    archive_request_id: str | None = Field(
        default=None,
        description="Idempotent archive request identifier used for the rendered document.",
        examples=["arch_rjob_83ca965c50334c40a17d2b8cc94873a5_pdf"],
    )
    document_id: str | None = Field(
        default=None,
        description="Archived document identifier returned by lotus-archive.",
        examples=["doc_83ca965c50334c40a17d2b8cc94873a5"],
    )
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when lotus-archive confirmed document archival.",
        examples=["2026-04-22T09:00:04Z"],
    )


class ReportJobRerenderRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Support-safe business or operations reason for rerendering the archived job.",
        examples=["Template correction after approved disclosure wording change."],
    )


class ReportJobRegenerateRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description=(
            "Support-safe business or operations reason for regenerating the archived job from "
            "fresh upstream data."
        ),
        examples=["Refresh report after upstream position correction was certified."],
    )


class ReportJobReplayRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Support-safe business or operations reason for replaying a failed job.",
        examples=["Retry after upstream service recovered."],
    )


class ReportRerenderAttemptRecord(BaseModel):
    rerender_attempt_id: str
    report_job_id: str
    idempotency_key: str
    status: ReportRerenderAttemptStatus
    snapshot_id: str
    snapshot_hash: str
    previous_render_job_id: str | None = None
    previous_archive_document_id: str | None = None
    render_job_id: str
    render_output_format: str = "pdf"
    render_template_id: str = "portfolio-review"
    render_template_version: str = "v1"
    render_artifact_sha256: str | None = None
    render_bounded_determinism_fingerprint: str | None = None
    render_runtime_engine: str | None = None
    render_runtime_engine_version: str | None = None
    render_duration_ms: int | None = None
    archive_request_id: str | None = None
    archive_document_id: str | None = None
    archive_completed_at: datetime | None = None
    failure_category: ReportFailureCategory | None = None
    failure_message: str | None = None
    retry_eligible: bool = False
    requested_by: str
    reason: str
    correlation_id: str
    trace_id: str
    created_at: datetime
    updated_at: datetime


class ReportJobRerenderResponse(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Source archived report job rerendered from its immutable snapshot.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    rerender_attempt_id: str = Field(
        ...,
        description="Opaque rerender attempt identifier preserving repeat render identity.",
        examples=["rrnd_4f7c85b39f7d4e7b8d0bb420d34a1d2c"],
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key for this rerender command.",
        examples=["rerender-rjob_83ca965c50334c40a17d2b8cc94873a5-template-fix-1"],
    )
    status: ReportRerenderAttemptStatus = Field(
        ...,
        description="Current rerender attempt status.",
        examples=["archived"],
    )
    snapshot_id: str = Field(
        ...,
        description="Immutable snapshot reused by the rerender attempt.",
        examples=["rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf"],
    )
    snapshot_hash: str = Field(
        ...,
        description=(
            "Snapshot hash reused by the rerender attempt; upstream data is not recollected."
        ),
        examples=["sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"],
    )
    previous_render_job_id: str | None = Field(
        default=None,
        description="Original render job identifier superseded by this rerender attempt.",
    )
    previous_archive_document_id: str | None = Field(
        default=None,
        description="Original archive document identifier superseded by the new archive document.",
    )
    archive_consequence: Literal["correction"] = Field(
        "correction",
        description="Archive consequence of a successful rerender.",
        examples=["correction"],
    )
    failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Machine-readable rerender failure category when the attempt failed.",
    )
    failure_message: str | None = Field(
        default=None,
        description="Support-safe rerender failure message when the attempt failed.",
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether retry is currently permitted for this rerender attempt.",
    )
    render: ReportJobRenderInfo = Field(
        ...,
        description="New render identity and render metadata for this rerender attempt.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="New archive handoff and document identifiers for this rerender attempt.",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the rerender attempt was created.",
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp when the rerender attempt was last updated.",
    )


class ReportJobRegenerateResponse(BaseModel):
    source_report_job_id: str = Field(
        ...,
        description="Archived source report job used as the regeneration template.",
    )
    regenerated_report_job_id: str = Field(
        ...,
        description="New report job created for fresh upstream snapshot capture and rendering.",
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key for this regenerate command.",
    )
    status: ReportRegenerateStatus = Field(
        ...,
        description="Current status of the regenerated report job.",
    )
    previous_snapshot_id: str | None = Field(
        default=None,
        description="Snapshot identifier from the source report job, when available.",
    )
    new_snapshot_id: str | None = Field(
        default=None,
        description="Snapshot identifier captured for the regenerated report job.",
    )
    previous_snapshot_hash: str | None = Field(
        default=None,
        description="Snapshot hash from the source report job, when available.",
    )
    new_snapshot_hash: str | None = Field(
        default=None,
        description="Snapshot hash captured for the regenerated report job.",
    )
    previous_archive_document_id: str | None = Field(
        default=None,
        description="Archived source document superseded by the regenerated document.",
    )
    new_archive_document_id: str | None = Field(
        default=None,
        description="Archive document identifier produced by the regenerated job.",
    )
    archive_consequence: Literal["replacement"] = Field(
        "replacement",
        description="Archive consequence of a successful regenerated document.",
    )
    failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Machine-readable regenerate failure category when regeneration failed.",
    )
    failure_message: str | None = Field(
        default=None,
        description="Support-safe regenerate failure message when regeneration failed.",
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether retry is currently permitted for the regenerated job.",
    )
    render: ReportJobRenderInfo | None = Field(
        default=None,
        description="Render metadata for the regenerated report job.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="Archive metadata for the regenerated report job.",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the regenerated job was created.",
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp when the regenerated job was last updated.",
    )


class ReportJobReplayResponse(BaseModel):
    source_report_job_id: str = Field(
        ...,
        description="Failed source report job used as the replay template.",
    )
    replayed_report_job_id: str = Field(
        ...,
        description="New report job created or reused for the replay attempt.",
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key for this replay command.",
    )
    status: ReportJobStatus = Field(
        ...,
        description="Current status of the replayed report job.",
    )
    source_failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Failure category from the source failed report job.",
    )
    failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Machine-readable replay failure category when replay failed.",
    )
    failure_message: str | None = Field(
        default=None,
        description="Support-safe replay failure message when replay failed.",
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether retry or replay remains permitted for the replayed job.",
    )
    render: ReportJobRenderInfo | None = Field(
        default=None,
        description="Render metadata for the replayed report job.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="Archive metadata for the replayed report job.",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the replayed job was created.",
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp when the replayed job was last updated.",
    )


class ReportJobStatusResponse(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Opaque durable report job identifier.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    report_request_id: str = Field(
        ...,
        description="Opaque durable report request identifier linked to the job.",
        examples=["rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c"],
    )
    report_type: str = Field(
        ...,
        description="Report type handled by this job ledger record.",
        examples=["portfolio_review"],
    )
    portfolio_scope: dict[str, Any] = Field(
        ...,
        description="Portfolio scope submitted for the report job.",
        examples=[{"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}],
    )
    status: ReportJobStatus = Field(
        ...,
        description="Current product-safe job status.",
        examples=["accepted"],
    )
    failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Machine-readable failure category when the job is failed or cancelled.",
        examples=["cancelled"],
    )
    failure_message: str | None = Field(
        default=None,
        description="Support-safe failure message suitable for operators and product consumers.",
        examples=["Report job cancelled before render or archive processing."],
    )
    current_step: str = Field(
        ...,
        description="Current lifecycle step for support diagnostics.",
        examples=["accepted"],
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether retry or replay is currently permitted by the ledger.",
        examples=[False],
    )
    cancel_requested: bool = Field(
        ...,
        description="Whether cancellation has been requested and recorded.",
        examples=[False],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the job was created.",
        examples=["2026-04-22T09:00:00Z"],
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp for the latest job state update.",
        examples=["2026-04-22T09:00:00Z"],
    )
    started_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when processing started, if processing has begun.",
        examples=["2026-04-22T09:00:05Z"],
    )
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the job completed, if complete.",
        examples=["2026-04-22T09:01:15Z"],
    )
    cancelled_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the job was cancelled, if cancelled.",
        examples=["2026-04-22T09:00:30Z"],
    )
    correlation_id: str = Field(
        ...,
        description="Correlation identifier captured at request creation.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier captured at request creation.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )
    render: ReportJobRenderInfo | None = Field(
        default=None,
        description="Support-safe render metadata when a rendered artifact was requested.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="Support-safe archive metadata when the rendered artifact was archived.",
    )


class ReportStatusEvent(BaseModel):
    status_event_id: str = Field(
        ...,
        description="Opaque append-only status event identifier.",
        examples=["rse_d7e9c3b87d864b098997d4fe5bd2de2a"],
    )
    report_job_id: str = Field(
        ...,
        description="Report job identifier associated with this event.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    from_status: ReportJobStatus | None = Field(
        default=None,
        description="Previous job status when this event is a transition.",
        examples=["accepted"],
    )
    to_status: ReportJobStatus = Field(
        ...,
        description="New job status recorded by this event.",
        examples=["cancelled"],
    )
    event_type: str = Field(
        ...,
        description="Machine-readable lifecycle event type.",
        examples=["job_cancelled"],
    )
    event_schema_version: str = Field(
        ...,
        description=(
            "Version of the support-safe lifecycle event payload contract. Legacy rows are "
            "returned as report-status-event.legacy.v0 with payload_posture=legacy_message_only."
        ),
        examples=["report-status-event.v1"],
    )
    event_family: str = Field(
        ...,
        description="Bounded lifecycle event family used by operators and future outbox consumers.",
        examples=["job_lifecycle"],
    )
    event_payload: dict[str, Any] = Field(
        ...,
        description=(
            "Support-safe typed payload for the event type. It carries identifiers and lifecycle "
            "facts needed by diagnostics without requiring clients to parse message text."
        ),
        examples=[
            {
                "event_type": "job_cancelled",
                "from_status": "accepted",
                "to_status": "cancelled",
                "current_step": "cancelled",
            }
        ],
    )
    event_idempotency_key: str | None = Field(
        default=None,
        description=(
            "Optional support-safe idempotency or deduplication key for lifecycle events that "
            "represent retry/replay/regenerate relationships."
        ),
        examples=["batch-item-replay:rbit_replay:rjob_source"],
    )
    message: str | None = Field(
        default=None,
        description="Support-safe event message.",
        examples=["Report job cancelled before render or archive processing."],
    )
    actor: str = Field(
        ...,
        description="Actor or system principal that caused the event.",
        examples=["advisor-123"],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the status event was appended.",
        examples=["2026-04-22T09:00:30Z"],
    )
    correlation_id: str = Field(
        ...,
        description="Correlation identifier associated with this status event.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier associated with this status event.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ReportJobStatusEventsResponse(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Report job identifier whose event history is returned.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    events: list[ReportStatusEvent] = Field(
        ...,
        description="Append-only lifecycle events ordered by creation time.",
        examples=[REPORT_JOB_STATUS_EVENTS_RESPONSE_EXAMPLE["events"]],
    )


class ReportJobSnapshotDiagnostics(BaseModel):
    snapshot_id: str = Field(
        ...,
        description="Opaque durable snapshot identifier associated with the report job.",
        examples=["rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf"],
    )
    snapshot_hash: str = Field(
        ...,
        description="Canonical hash of the captured snapshot payload; raw payload is not returned.",
        examples=["sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"],
    )
    supportability_status: str = Field(
        ...,
        description="Supportability posture recorded on the durable snapshot.",
        examples=["complete"],
    )
    completeness_status: str = Field(
        ...,
        description="Completeness posture recorded on the durable snapshot.",
        examples=["complete"],
    )
    captured_at: datetime = Field(
        ...,
        description="UTC timestamp when snapshot capture completed.",
        examples=["2026-04-22T09:00:03Z"],
    )


class ReportJobLineageDiagnostics(BaseModel):
    upstream_call_count: int = Field(
        ...,
        description="Number of durable upstream-call evidence rows linked to the snapshot.",
        examples=[3],
    )
    source_services: list[str] = Field(
        ...,
        description="Bounded service names observed in upstream lineage evidence.",
        examples=[["lotus-core", "lotus-performance", "lotus-risk"]],
    )
    supportability_status: str = Field(
        ...,
        description="Aggregated supportability posture from the durable snapshot summary.",
        examples=["complete"],
    )
    completeness_status: str = Field(
        ...,
        description="Aggregated completeness posture from the durable snapshot summary.",
        examples=["complete"],
    )
    failure_categories: list[str] = Field(
        ...,
        description="Distinct non-empty upstream failure categories excluding normal `none` rows.",
        examples=[["upstream_unavailable"]],
    )


class ReportJobRelationshipRecord(BaseModel):
    relationship_id: str = Field(
        ...,
        description="Opaque durable relationship identifier.",
        examples=["rjr_3e6d73f12e344448bc9a6607959dfb6a"],
    )
    relationship_type: ReportJobRelationshipType = Field(
        ...,
        description="Bounded source-to-derived report job relationship type.",
        examples=["regenerate_replacement"],
    )
    source_report_job_id: str = Field(
        ...,
        description="Opaque source report job identifier.",
        examples=["rjob_source_001"],
    )
    derived_report_job_id: str = Field(
        ...,
        description="Opaque regenerated or replayed report job identifier.",
        examples=["rjob_derived_001"],
    )
    source_status: ReportJobStatus = Field(
        ...,
        description="Current or command-time source job status captured for support navigation.",
        examples=["archived"],
    )
    derived_status: ReportJobStatus = Field(
        ...,
        description="Current or latest derived job status captured for support navigation.",
        examples=["archived"],
    )
    source_failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Source job failure category when the relationship starts from failed work.",
    )
    derived_failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Derived job failure category when the derived work fails.",
    )
    archive_consequence: str | None = Field(
        default=None,
        description="Archive consequence such as `replacement` when applicable.",
        examples=["replacement"],
    )
    previous_archive_document_id: str | None = Field(
        default=None,
        description="Source archive document superseded by the derived work when applicable.",
    )
    new_archive_document_id: str | None = Field(
        default=None,
        description="Derived archive document produced by the relationship when applicable.",
    )
    actor: str = Field(
        ...,
        description="Actor or system principal that requested the relationship.",
        examples=["advisor-123"],
    )
    reason: str = Field(
        ...,
        description="Bounded operator reason captured from the command request.",
        examples=["Certified upstream position correction."],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the relationship was first recorded.",
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp when the relationship was last updated.",
    )


class ReportJobOperationLinks(BaseModel):
    status_url: str = Field(
        ...,
        description="Relative URL for the source-backed report job status contract.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    events_url: str = Field(
        ...,
        description="Relative URL for append-only report job lifecycle events.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/events"],
    )
    snapshot_url: str | None = Field(
        default=None,
        description="Relative URL for the durable snapshot when snapshot evidence exists.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/snapshot"],
    )
    lineage_url: str | None = Field(
        default=None,
        description="Relative URL for durable upstream lineage when snapshot evidence exists.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/lineage"],
    )


class ReportRerenderAttemptDiagnostics(BaseModel):
    rerender_attempt_id: str = Field(
        ...,
        description="Opaque rerender attempt identifier for support-safe correction audit.",
        examples=["rrnd_4f7c85b39f7d4e7b8d0bb420d34a1d2c"],
    )
    report_job_id: str = Field(
        ...,
        description="Source archived report job rerendered from its immutable snapshot.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    status: ReportRerenderAttemptStatus = Field(
        ...,
        description="Current rerender attempt status.",
        examples=["archived"],
    )
    snapshot_id: str = Field(
        ...,
        description="Immutable snapshot reused by the rerender attempt.",
        examples=["rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf"],
    )
    snapshot_hash: str = Field(
        ...,
        description="Snapshot hash reused by the rerender attempt.",
        examples=["sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"],
    )
    previous_render_job_id: str | None = Field(
        default=None,
        description="Original render job identifier superseded by the rerender attempt.",
    )
    previous_archive_document_id: str | None = Field(
        default=None,
        description="Original archive document identifier superseded by the rerender attempt.",
    )
    archive_consequence: Literal["correction"] = Field(
        "correction",
        description="Archive consequence of a successful rerender.",
        examples=["correction"],
    )
    failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Machine-readable rerender failure category when the attempt failed.",
    )
    failure_message: str | None = Field(
        default=None,
        description="Support-safe rerender failure message when the attempt failed.",
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether retry is currently permitted for this rerender attempt.",
    )
    render: ReportJobRenderInfo = Field(
        ...,
        description="New render identity and render metadata for this rerender attempt.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="New archive handoff and document identifiers for this rerender attempt.",
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the rerender attempt was created.",
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp when the rerender attempt was last updated.",
    )


class ReportJobDiagnosticsResponse(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Opaque durable report job identifier inspected by the diagnostics view.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    status: ReportJobStatusResponse = Field(
        ...,
        description="Source-backed product-safe job status and render/archive summary.",
        examples=[REPORT_JOB_STATUS_RESPONSE_EXAMPLE],
    )
    event_count: int = Field(
        ...,
        description="Count of append-only lifecycle events recorded for the job.",
        examples=[4],
    )
    latest_event: ReportStatusEvent | None = Field(
        default=None,
        description="Most recent lifecycle event when event history exists.",
    )
    snapshot: ReportJobSnapshotDiagnostics | None = Field(
        default=None,
        description="Support-safe snapshot posture without raw snapshot payload or storage refs.",
    )
    lineage: ReportJobLineageDiagnostics | None = Field(
        default=None,
        description="Support-safe lineage summary without request or response payloads.",
    )
    relationships: list[ReportJobRelationshipRecord] = Field(
        default_factory=list,
        description=(
            "Support-safe source-to-derived job relationships for regenerate, replay, and "
            "batch-item replay navigation."
        ),
        examples=[REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE["relationships"]],
    )
    rerender_attempts: list[ReportRerenderAttemptDiagnostics] = Field(
        default_factory=list,
        description=(
            "Most recent support-safe rerender attempts for correction-document audit. "
            "Idempotency keys, correlation/trace values, storage keys, and raw payloads are not "
            "exposed in this diagnostics read model."
        ),
        examples=[REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE["rerender_attempts"]],
    )
    render: ReportJobRenderInfo | None = Field(
        default=None,
        description="Support-safe render metadata copied from the report job ledger.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="Support-safe archive handoff and document identifiers from the job ledger.",
    )
    diagnostic_flags: list[str] = Field(
        ...,
        description="Machine-readable support flags such as `snapshot_not_captured`.",
        examples=[["snapshot_not_captured"]],
    )
    operation_links: ReportJobOperationLinks = Field(
        ...,
        description="Related source-backed operator endpoints for deeper evidence review.",
        examples=[REPORT_JOB_DIAGNOSTICS_RESPONSE_EXAMPLE["operation_links"]],
    )


ReportPortfolioMemorySupportabilityState = Literal[
    "READY",
    "PENDING_REVIEW",
    "DEGRADED",
    "EMPTY",
]


class ReportPortfolioMemorySourceRef(BaseModel):
    source_system: str = Field(
        ...,
        description="Lotus system that owns the referenced source fact.",
        examples=["lotus-report"],
    )
    source_type: str = Field(
        ...,
        description="Bounded source type for the referenced fact.",
        examples=["REPORT_STATUS_EVENT"],
    )
    source_id: str = Field(
        ...,
        description="Opaque source identifier in the owning system.",
        examples=["rse_d7e9c3b87d864b098997d4fe5bd2de2a"],
    )
    content_hash: str | None = Field(
        default=None,
        description="Canonical content hash when the referenced source exposes one.",
        examples=["sha256:7ce6f9c6c5385fca0c5751f3446d575d00e27d467131ba42e0fae019ca27db21"],
    )


class ReportPortfolioMemoryArtifactRef(BaseModel):
    artifact_system: str = Field(
        ...,
        description="Lotus system that owns the referenced artifact.",
        examples=["lotus-archive"],
    )
    artifact_type: str = Field(
        ...,
        description="Bounded artifact type.",
        examples=["ARCHIVED_REPORT_DOCUMENT"],
    )
    artifact_id: str = Field(
        ...,
        description="Opaque artifact identifier in the owning system.",
        examples=["doc_83ca965c50334c40a17d2b8cc94873a5"],
    )
    content_hash: str | None = Field(
        default=None,
        description="Artifact content hash when available.",
        examples=["sha256:artifact-portfolio-review"],
    )


class ReportPortfolioMemoryGovernancePolicy(BaseModel):
    event_identity_scheme: str = Field(
        ...,
        description="Stable event identity composition rule used by this source-event family.",
        examples=["source_system:source_type:source_id:content_hash_or_content_hash_unavailable"],
    )
    retention_policy: str = Field(
        ...,
        description="Retention policy identifier for report-owned memory source lineage.",
        examples=["DPM_PORTFOLIO_MEMORY_SOURCE_LINEAGE_7Y"],
    )
    redaction_policy: str = Field(
        ...,
        description="Redaction policy applied to this support-safe event surface.",
        examples=["NO_RAW_PAYLOADS"],
    )
    audit_policy: str = Field(
        ...,
        description="Audit policy expected for read/export access.",
        examples=["AUDIT_READ_AND_EXPORT"],
    )
    access_classification: str = Field(
        ...,
        description="Access classification for client-confidential internal report events.",
        examples=["CLIENT_CONFIDENTIAL_INTERNAL"],
    )


class ReportPortfolioMemoryEvent(BaseModel):
    event_id: str = Field(
        ...,
        description="Stable report-owned event identifier for portfolio-memory ingestion.",
        examples=[
            "report-memory:rjob_83ca965c50334c40a17d2b8cc94873a5:"
            "rse_d7e9c3b87d864b098997d4fe5bd2de2a"
        ],
    )
    event_identity: str = Field(
        ...,
        description="Stable deduplication identity for the report-owned source event.",
        examples=[
            "lotus-report:REPORT_STATUS_EVENT:"
            "rse_d7e9c3b87d864b098997d4fe5bd2de2a:"
            "sha256:7ce6f9c6c5385fca0c5751f3446d575d00e27d467131ba42e0fae019ca27db21"
        ],
    )
    event_type: str = Field(
        ...,
        description="Bounded report-owned event type mapped from the report lifecycle.",
        examples=["REPORT_JOB_ARCHIVED"],
    )
    event_time: datetime = Field(
        ...,
        description="UTC timestamp when the report-owned source event occurred.",
        examples=["2026-04-22T09:00:04Z"],
    )
    actor: str = Field(
        ...,
        description="Actor or system principal that caused the report lifecycle event.",
        examples=["advisor-123"],
    )
    source_system: str = Field(
        "lotus-report",
        description="Owning source system for this event family.",
        examples=["lotus-report"],
    )
    source_type: str = Field(
        ...,
        description="Bounded source type for the owning report event.",
        examples=["REPORT_STATUS_EVENT"],
    )
    source_id: str = Field(
        ...,
        description="Opaque owning source identifier.",
        examples=["rse_d7e9c3b87d864b098997d4fe5bd2de2a"],
    )
    portfolio_id: str | None = Field(
        default=None,
        description="Primary portfolio identifier when the report scope is portfolio-bound.",
        examples=["PB_SG_GLOBAL_BAL_001"],
    )
    report_job_id: str = Field(
        ...,
        description="Report job that produced this source event.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    report_type: str = Field(
        ...,
        description="Report type associated with the source event.",
        examples=["proof_pack"],
    )
    status: ReportJobStatus = Field(
        ...,
        description="Report job status after this lifecycle event.",
        examples=["archived"],
    )
    supportability_state: ReportPortfolioMemorySupportabilityState = Field(
        ...,
        description="Supportability posture of this event for downstream portfolio memory.",
        examples=["READY"],
    )
    summary: str = Field(
        ...,
        description="Support-safe event summary without raw report payloads.",
        examples=["Report job archived for proof_pack."],
    )
    reason_codes: list[str] = Field(
        ...,
        description="Machine-readable reason codes for the event and supportability posture.",
        examples=[["REPORT_JOB_ARCHIVED"]],
    )
    source_refs: list[ReportPortfolioMemorySourceRef] = Field(
        ...,
        description="Support-safe source references required to audit the event.",
    )
    artifact_refs: list[ReportPortfolioMemoryArtifactRef] = Field(
        default_factory=list,
        description="Support-safe render/archive artifact references linked to this event.",
    )
    content_hash: str = Field(
        ...,
        description="Canonical hash over the support-safe event envelope.",
        examples=["sha256:7ce6f9c6c5385fca0c5751f3446d575d00e27d467131ba42e0fae019ca27db21"],
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Bounded operational metadata such as correlation and trace identifiers.",
        examples=[{"correlation_id": "corr-portfolio-review-1"}],
    )


class ReportPortfolioMemoryEventsResponse(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Report job whose report-owned portfolio-memory source events are returned.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    portfolio_id: str | None = Field(
        default=None,
        description="Primary portfolio identifier when the report scope is portfolio-bound.",
        examples=["PB_SG_GLOBAL_BAL_001"],
    )
    report_type: str = Field(
        ...,
        description="Report type associated with the returned event family.",
        examples=["proof_pack"],
    )
    event_count: int = Field(
        ...,
        description="Number of report-owned source events returned.",
        examples=[3],
    )
    supportability_state: ReportPortfolioMemorySupportabilityState = Field(
        ...,
        description="Aggregated supportability posture for the returned event family.",
        examples=["READY"],
    )
    source_systems: list[str] = Field(
        ...,
        description="Source systems represented by this report-owned event response.",
        examples=[["lotus-report"]],
    )
    reason_codes: list[str] = Field(
        ...,
        description="Aggregated machine-readable reason codes for the response posture.",
        examples=[["REPORT_EVENT_FAMILY_READY"]],
    )
    governance_policy: ReportPortfolioMemoryGovernancePolicy = Field(
        ...,
        description="Governance policies applied to the report-owned event family.",
        examples=[REPORT_PORTFOLIO_MEMORY_EVENTS_RESPONSE_EXAMPLE["governance_policy"]],
    )
    content_hash: str = Field(
        ...,
        description="Canonical hash over the returned support-safe event family.",
        examples=[REPORT_PORTFOLIO_MEMORY_EVENTS_RESPONSE_EXAMPLE["content_hash"]],
    )
    generated_at: datetime = Field(
        ...,
        description="UTC timestamp when this response was generated.",
        examples=["2026-04-22T09:00:04Z"],
    )
    events: list[ReportPortfolioMemoryEvent] = Field(
        ...,
        description="Report-owned portfolio-memory source events ordered by event time.",
        examples=[REPORT_PORTFOLIO_MEMORY_EVENTS_RESPONSE_EXAMPLE["events"]],
    )


class ReportJobListFilters(BaseModel):
    tenant_id: str | None = Field(
        default=None,
        description="Tenant filter used to isolate jobs for one tenant scope.",
        examples=["tenant-sg"],
    )
    region: str | None = Field(
        default=None,
        description="Region filter used to isolate jobs for one operating region.",
        examples=["APAC"],
    )
    status: ReportJobStatus | None = Field(
        default=None,
        description="Current job-status filter.",
        examples=["accepted"],
    )
    report_type: str | None = Field(
        default=None,
        description="Report-type filter for the job search.",
        examples=["portfolio_review"],
    )
    portfolio_id: str | None = Field(
        default=None,
        description="Portfolio identifier contained in the submitted portfolio scope.",
        examples=["PB_SG_GLOBAL_BAL_001"],
    )
    as_of_date: date | None = Field(
        default=None,
        description="Business as-of date filter for the report request.",
        examples=["2026-04-22"],
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Idempotency key filter for duplicate-request diagnostics.",
        examples=["portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22"],
    )
    correlation_id: str | None = Field(
        default=None,
        description="Correlation identifier filter for end-to-end operational tracing.",
        examples=["corr-portfolio-review-1"],
    )
    created_from: datetime | None = Field(
        default=None,
        description="Inclusive lower UTC bound for report-request creation time.",
        examples=["2026-04-22T00:00:00Z"],
    )
    created_to: datetime | None = Field(
        default=None,
        description="Inclusive upper UTC bound for report-request creation time.",
        examples=["2026-04-23T00:00:00Z"],
    )
    limit: int = Field(
        default=25,
        description="Maximum number of jobs returned by this bounded first-wave search.",
        examples=[25],
    )


class ReportJobListItem(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Opaque durable report job identifier.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    report_request_id: str = Field(
        ...,
        description="Opaque durable report request identifier linked to the job.",
        examples=["rrq_4f7c85b39f7d4e7b8d0bb420d34a1d2c"],
    )
    report_type: str = Field(
        ...,
        description="Report type handled by the job.",
        examples=["portfolio_review"],
    )
    tenant_id: str = Field(
        ...,
        description="Tenant identifier captured when the request was created.",
        examples=["tenant-sg"],
    )
    region: str = Field(
        ...,
        description="Operating region captured when the request was created.",
        examples=["APAC"],
    )
    portfolio_scope: dict[str, Any] = Field(
        ...,
        description="Submitted portfolio scope for the report job.",
        examples=[{"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}],
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date submitted for the report job.",
        examples=["2026-04-22"],
    )
    status: ReportJobStatus = Field(
        ...,
        description="Current product-safe report job status.",
        examples=["accepted"],
    )
    failure_category: ReportFailureCategory | None = Field(
        default=None,
        description="Machine-readable failure category when the job failed or was cancelled.",
        examples=[None],
    )
    current_step: str = Field(
        ...,
        description="Current lifecycle step for support diagnostics.",
        examples=["accepted"],
    )
    retry_eligible: bool = Field(
        ...,
        description="Whether retry or replay is currently permitted.",
        examples=[False],
    )
    cancel_requested: bool = Field(
        ...,
        description="Whether cancellation has been requested and recorded.",
        examples=[False],
    )
    idempotency_key: str = Field(
        ...,
        description="Caller-supplied idempotency key associated with the job.",
        examples=["portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22"],
    )
    correlation_id: str = Field(
        ...,
        description="Correlation identifier captured when the request was created.",
        examples=["corr-portfolio-review-1"],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the job was created.",
        examples=["2026-04-22T09:00:00Z"],
    )
    updated_at: datetime = Field(
        ...,
        description="UTC timestamp when the job was last updated.",
        examples=["2026-04-22T09:00:00Z"],
    )
    render: ReportJobRenderInfo | None = Field(
        default=None,
        description="Support-safe render summary when a rendered artifact was requested.",
    )
    archive: ReportJobArchiveInfo | None = Field(
        default=None,
        description="Support-safe archive summary when the rendered artifact was archived.",
    )


class ReportJobListResponse(BaseModel):
    count: int = Field(
        ...,
        description="Number of jobs returned in this bounded response.",
        examples=[1],
    )
    applied_filters: ReportJobListFilters = Field(
        ...,
        description="Normalized filters applied to the job search.",
        examples=[REPORT_JOB_LIST_FILTERS_EXAMPLE],
    )
    items: list[ReportJobListItem] = Field(
        ...,
        description="Bounded list of support-safe report job summaries.",
        examples=[REPORT_JOB_LIST_RESPONSE_EXAMPLE["items"]],
    )


class ReportJobLedgerRecord(BaseModel):
    request_id: str
    job_id: str
    report_type: str
    portfolio_scope: dict[str, Any]
    requested_output_formats: list[str]
    as_of_date: date
    reporting_currency: str | None = None
    options: dict[str, Any]
    trigger_type: str
    triggered_by: str
    caller_application: str
    tenant_id: str
    region: str
    booking_center_code: str | None = None
    role: str | None = None
    idempotency_key: str
    request_hash: str
    status: ReportJobStatus
    failure_category: ReportFailureCategory | None = None
    failure_message: str | None = None
    current_step: str
    retry_eligible: bool
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    correlation_id: str
    trace_id: str
    render_job_id: str | None = None
    render_output_format: str | None = None
    render_template_id: str | None = None
    render_template_version: str | None = None
    render_artifact_sha256: str | None = None
    render_bounded_determinism_fingerprint: str | None = None
    render_runtime_engine: str | None = None
    render_runtime_engine_version: str | None = None
    render_duration_ms: int | None = None
    archive_request_id: str | None = None
    archive_document_id: str | None = None
    archive_completed_at: datetime | None = None


class ReportJobArchiveStatusRecord(BaseModel):
    """Bounded source projection used by batch status composition."""

    report_job_id: str
    status: ReportJobStatus
    archive_document_id: str | None = None
