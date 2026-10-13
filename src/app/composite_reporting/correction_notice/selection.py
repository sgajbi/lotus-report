"""Exact retained capture selection, independent of financial selection models."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NoticeId = Annotated[str, Field(strict=True, min_length=1, max_length=256)]
NoticeDigest = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
PROFILE = "RETAINED_SAME_SCOPE_CORRECTION_NOTICE_V1"


class NoticeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelectionTuple(NoticeModel):
    report_job_id: NoticeId
    snapshot_id: NoticeId
    report_revision_id: NoticeId
    snapshot_hash: NoticeDigest


class CorrectionNoticeSelection(NoticeModel):
    comparison_profile: Literal["RETAINED_SAME_SCOPE_CORRECTION_NOTICE_V1"]
    tenant_id: NoticeId
    composite_id: NoticeId
    period_start: date
    period_end: date
    reporting_currency: str = Field(strict=True, pattern=r"^[A-Z]{3}$")
    before: SelectionTuple
    after: SelectionTuple
    source_document_ids: list[NoticeId] = Field(min_length=4, max_length=4)
    relationship_ids: list[NoticeId] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def require_exact_pair(self) -> "CorrectionNoticeSelection":
        if self.period_end < self.period_start or self.before == self.after:
            raise ValueError("COMPOSITE_NOTICE_PAIR_INVALID")
        if len(set(self.source_document_ids)) != 4 or len(set(self.relationship_ids)) != 3:
            raise ValueError("COMPOSITE_NOTICE_PATH_DUPLICATED")
        return self


SELECTOR = {
    "report_family_id": "composite_review",
    "report_type": "composite_review",
    "ordering_mode_id": "single_composite",
    "comparison_profile": PROFILE,
    "report_data_contract_version": "composite_review.v9",
    "input_snapshot_contract_version": "composite_review.v9",
    "template_id": "composite-review",
    "template_version": "v9",
    "output_format": "xlsx",
    "locale": "en-SG",
    "brand": "private_banking",
}
SCOPE_AXES = (
    "tenant_id",
    "composite_id",
    "period_start",
    "period_end",
    "reporting_currency",
    "return_view",
    "methodology",
    "engine_version",
)
SOURCE_TABLES = (
    "Summary",
    "PeriodReturns",
    "Contribution",
    "Methods",
    "Lineage",
    "Disclosures",
    "AnnualReturns",
    "Risk",
    "Members",
    "EligibilityReasons",
    "Attribution",
    "Restatement",
    "SinceInception",
    "MembershipHistory",
)
