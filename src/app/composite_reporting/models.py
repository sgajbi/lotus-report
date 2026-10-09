"""Consumed Performance wire contracts and exact caller-selected report identity.

Financial values validate as finite decimals, but the accepted raw response is
retained separately: typed projection must not rewrite source precision or bytes.
Additive producer fields survive custody; report selectors forbid unknown fields.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
FeeView = Literal["GROSS", "NET_ACTUAL", "NET_MODEL_FEE"]
SourceStatus = Literal["READY", "DEGRADED", "BLOCKED"]


def _finite_source_number(value: Any) -> Any:
    # Performance serializes Decimal as JSON text. Binary floats and booleans
    # are refused, rather than silently laundering lost precision into Decimal.
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Financial evidence must be finite decimal text or an exact integer")
    return value


SourceNumber = Annotated[
    Decimal, BeforeValidator(_finite_source_number), Field(allow_inf_nan=False)
]


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class CompositeWindowPin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materialization_id: UUID
    period_start: date
    period_end: date
    restatement_sequence: int = Field(ge=1, strict=True)
    definition_content_hash: Digest
    membership_content_hash: Digest
    attestation_content_hash: Digest
    source_cut_id: Identifier
    method_binding: dict[str, str] = Field(min_length=1)
    retained_receipt_fingerprint: Digest

    @model_validator(mode="after")
    def validate_window(self) -> CompositeWindowPin:
        if self.period_end < self.period_start:
            raise ValueError("Composite window ends before it starts")
        if any(not key.strip() or not value.strip() for key, value in self.method_binding.items()):
            raise ValueError("Method binding must retain nonblank source identity")
        return self


class CompositeReportSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: Identifier = Field(
        description="Must match admitted caller tenant; no body authority."
    )
    composite_id: Identifier
    calculation_id: UUID
    period_start: date
    period_end: date
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    return_view: FeeView
    methodology: Identifier
    engine_version: Identifier
    calculation_fingerprint: Digest
    response_digest: Digest = Field(
        description="Expected canonical raw response SHA-256, not approval."
    )
    windows: list[CompositeWindowPin] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_vector(self) -> CompositeReportSelection:
        if len({item.materialization_id for item in self.windows}) != len(self.windows):
            raise ValueError("Materialization identities must be unique")
        if (self.windows[0].period_start, self.windows[-1].period_end) != (
            self.period_start,
            self.period_end,
        ):
            raise ValueError("Exact windows must cover the requested horizon")
        for previous, current in zip(self.windows, self.windows[1:]):
            if current.period_start != previous.period_end + timedelta(days=1):
                raise ValueError("Exact windows must be chronological and contiguous")
        return self

    def performance_request(self) -> dict[str, Any]:
        return {
            "calculation_id": str(self.calculation_id),
            "composite_id": self.composite_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "reporting_currency": self.reporting_currency,
            "return_view": self.return_view,
            "materialization_ids": [str(item.materialization_id) for item in self.windows],
        }


class CompositeMemberContribution(SourceModel):
    portfolio_id: str = Field(min_length=1)
    period_start: date
    period_end: date
    return_value: SourceNumber
    beginning_market_value: SourceNumber
    beginning_asset_weight: SourceNumber
    contribution: SourceNumber
    source_snapshot_id: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=1)
    restatement_version: str = Field(min_length=1)
    restatement_sequence: int = Field(ge=1, strict=True)
    calculation_id: str | None = None
    source_authority_identity: dict[str, Any] | None = None


class CompositePeriod(SourceModel):
    period_start: date
    period_end: date
    status: SourceStatus
    return_value: SourceNumber | None
    cumulative_return: SourceNumber | None
    beginning_market_value: SourceNumber
    ending_market_value: SourceNumber
    member_count: int = Field(ge=0, strict=True)
    excluded_member_count: int = Field(ge=0, strict=True)
    dispersion_equal_weight: SourceNumber | None
    return_view: FeeView | None
    reporting_currency: str | None
    source_fingerprints: list[str]
    restatement_versions: list[str]
    restatement_sequence: int | None = Field(ge=1, strict=True)
    reason_codes: list[str]
    member_contributions: list[CompositeMemberContribution]


class CompositeSelectionManifest(SourceModel):
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    windows: list[CompositeWindowPin]
    engine_version: str = Field(min_length=1)
    calculation_fingerprint: Digest


class CompositeCalculatedResponse(SourceModel):
    calculation_id: UUID
    composite_id: str = Field(min_length=1)
    status: SourceStatus
    period_start: date
    period_end: date
    cumulative_return: SourceNumber | None
    reason_codes: list[str]
    periods: list[CompositePeriod] = Field(min_length=1)
    methodology: str = Field(min_length=1)
    selection_manifest: CompositeSelectionManifest


class CompositeReviewJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: CompositeReportSelection = Field(
        description="Exact retained calculation. Official publication authority is unavailable."
    )
    requested_output_formats: list[Literal["json", "xlsx"]] = Field(
        default=["json"],
        min_length=1,
        max_length=1,
        description="Select one internal calculated-review output: JSON dataset or XLSX workbook.",
    )
    options: dict[str, Any] = Field(default_factory=dict, description="Governed retention options.")
