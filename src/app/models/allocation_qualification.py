"""Source-owned allocation coverage and Report's presentation qualification."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CoverageState = Literal[
    "COMPLETE", "MEASURED_ZERO", "CARRY_FORWARD", "LOADED_EMPTY", "PARTIAL", "UNAVAILABLE"
]
CoverageReason = Literal[
    "all_source_positions_covered",
    "source_measured_zero",
    "latest_source_snapshot_precedes_as_of_date",
    "source_snapshot_has_no_open_positions",
    "portfolio_snapshot_missing",
    "open_position_coverage_gap",
    "market_value_missing",
    "valuation_status_not_valued",
    "no_source_snapshot",
]
STATE_REASONS: dict[str, set[str]] = {
    "COMPLETE": {"all_source_positions_covered"},
    "MEASURED_ZERO": {"source_measured_zero"},
    "CARRY_FORWARD": {"latest_source_snapshot_precedes_as_of_date"},
    "LOADED_EMPTY": {"source_snapshot_has_no_open_positions"},
    "PARTIAL": {
        "portfolio_snapshot_missing",
        "open_position_coverage_gap",
        "market_value_missing",
        "valuation_status_not_valued",
    },
    "UNAVAILABLE": {"no_source_snapshot", "open_position_coverage_gap"},
}


class AllocationValuationCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    coverage_state: CoverageState
    coverage_reason: CoverageReason
    snapshot_row_count: int = Field(ge=0, strict=True)
    expected_open_position_count: int = Field(ge=0, strict=True)
    valued_position_count: int = Field(ge=0, strict=True)
    unvalued_position_count: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def coherent_source_statement(self) -> "AllocationValuationCoverage":
        if self.coverage_reason not in STATE_REASONS[self.coverage_state]:
            raise ValueError("incompatible allocation coverage reason")
        if self.valued_position_count + self.unvalued_position_count != self.snapshot_row_count:
            raise ValueError("incompatible allocation coverage counts")
        if self.coverage_state in {"COMPLETE", "MEASURED_ZERO", "CARRY_FORWARD"}:
            if (
                not self.snapshot_row_count
                or self.unvalued_position_count
                or self.expected_open_position_count > self.snapshot_row_count
            ):
                raise ValueError("incomplete claimed covered allocation")
        if self.coverage_state == "LOADED_EMPTY" and (
            self.snapshot_row_count or self.expected_open_position_count
        ):
            raise ValueError("nonempty claimed empty allocation")
        if self.coverage_state == "UNAVAILABLE" and self.snapshot_row_count:
            raise ValueError("observed claimed unavailable allocation")
        if self.coverage_reason in {"market_value_missing", "valuation_status_not_valued"}:
            if not self.unvalued_position_count:
                raise ValueError("missing unvalued allocation count")
        if self.coverage_reason == "open_position_coverage_gap":
            if self.expected_open_position_count <= self.snapshot_row_count:
                raise ValueError("missing allocation coverage gap")
        return self


class AllocationQualification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: Literal["allocation-valuation-v1"] = "allocation-valuation-v1"
    status: Literal["complete", "partial", "missing", "invalid"]
    reason_code: Literal[
        "allocation_valuation_complete",
        "allocation_valuation_partial",
        "allocation_valuation_carry_forward",
        "allocation_valuation_missing",
        "allocation_valuation_invalid",
        "allocation_numeric_unavailable",
    ]
    coverage: AllocationValuationCoverage | None = None
    source_portfolio_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_as_of_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    source_reporting_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    client_publication_allowed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def coherent_presentation(self) -> "AllocationQualification":
        reasons = {
            "complete": {"allocation_valuation_complete"},
            "partial": {
                "allocation_valuation_partial",
                "allocation_valuation_carry_forward",
                "allocation_numeric_unavailable",
            },
            "missing": {"allocation_valuation_missing"},
            "invalid": {"allocation_valuation_invalid"},
        }
        if self.reason_code not in reasons[self.status]:
            raise ValueError("incompatible allocation qualification reason")
        if self.source_as_of_date is not None:
            date.fromisoformat(self.source_as_of_date)
        if self.status == "complete" and (
            self.reason_code != "allocation_valuation_complete"
            or self.coverage is None
            or self.coverage.coverage_state not in {"COMPLETE", "MEASURED_ZERO", "LOADED_EMPTY"}
        ):
            raise ValueError("incompatible complete allocation qualification")
        if self.client_publication_allowed and (
            self.status != "complete"
            or not self.source_portfolio_id
            or not self.source_as_of_date
            or not self.source_reporting_currency
        ):
            raise ValueError("unbound allocation publication")
        return self


class PortfolioReviewAllocation(BaseModel):
    model_config = ConfigDict(extra="allow")

    qualification: AllocationQualification | None = None
    valuation_coverage: AllocationValuationCoverage | None = None
