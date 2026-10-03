"""Named history evidence; Performance owns its dates, calendar and calculation basis."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

WORKSPACE_HISTORY_PERIODS = (
    "1D",
    "2D",
    "5D",
    "10D",
    "1M",
    "3M",
    "6M",
    "YTD",
    "1Y",
    "2Y",
    "5Y",
    "10Y",
    "SI",
    "EXPLICIT",
)
MAX_HISTORY_PERIODS = len(WORKSPACE_HISTORY_PERIODS)
HistoryPeriod = Annotated[
    str, Field(pattern="^(" + "|".join(WORKSPACE_HISTORY_PERIODS) + ")$", max_length=8)
]

HistoryReason = Literal[
    "covered_window_matches_requested_window",
    "no_observations_in_requested_window",
    "leading_history_missing",
    "interior_history_missing",
    "trailing_history_missing",
    "venue_calendar_not_attested",
    "explicit_ignored_dates_applied",
    "beginning_market_value_baseline_applied",
]


class SourceTwrReturn(BaseModel):
    base: Annotated[FiniteFloat, Field(strict=True)]


class SourceTwrSummary(BaseModel):
    cumulative_return: SourceTwrReturn
    annualized_return: SourceTwrReturn | None = None


class PerformanceHistoryCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["complete", "partial", "unknown"]
    calculation_basis: Literal["requested_window", "available_window"]
    requested_start_date: date
    requested_end_date: date
    covered_start_date: date | None
    covered_end_date: date | None
    effective_start_date: date | None
    effective_end_date: date | None
    calendar_basis: Literal["natural_days", "business_weekdays"]
    missing_required_observation_count: int = Field(ge=0, strict=True)
    missing_required_observation_dates_sample: list[date] = Field(max_length=10)
    reason_codes: list[HistoryReason] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_source_statement(self) -> "PerformanceHistoryCoverage":
        self._validate_bounds()
        self._validate_missing_sample()
        self._validate_status()
        return self

    def _validate_bounds(self) -> None:
        if self.requested_start_date > self.requested_end_date:
            raise ValueError("invalid requested history window")
        for start, end in (
            (self.covered_start_date, self.covered_end_date),
            (self.effective_start_date, self.effective_end_date),
        ):
            if (start is None) != (end is None) or (start and end and start > end):
                raise ValueError("invalid observed history bounds")
        if self.effective_start_date is not None and self.effective_end_date is not None:
            if not (
                self.requested_start_date
                <= self.effective_start_date
                <= self.effective_end_date
                <= self.requested_end_date
            ):
                raise ValueError("effective history outside requested window")
            if (
                self.covered_start_date is None
                or self.covered_end_date is None
                or not (
                    self.covered_start_date
                    <= self.effective_start_date
                    <= self.effective_end_date
                    <= self.covered_end_date
                )
            ):
                raise ValueError("effective history outside covered observations")

    def _validate_missing_sample(self) -> None:
        sample = self.missing_required_observation_dates_sample
        if sample != sorted(set(sample)) or len(sample) > self.missing_required_observation_count:
            raise ValueError("invalid missing-observation sample")
        if any(not self.requested_start_date <= day <= self.requested_end_date for day in sample):
            raise ValueError("missing-observation sample outside requested window")

    def _validate_status(self) -> None:
        reasons = set(self.reason_codes)
        if len(reasons) != len(self.reason_codes):
            raise ValueError("duplicate history reason")
        gaps = {"leading_history_missing", "interior_history_missing", "trailing_history_missing"}
        if self.status == "complete":
            if (
                self.calculation_basis != "requested_window"
                or self.missing_required_observation_count
            ):
                raise ValueError("contradictory complete history")
            if self.effective_start_date is None:
                raise ValueError("complete history without observations")
            if "covered_window_matches_requested_window" not in reasons or reasons & (
                gaps | {"no_observations_in_requested_window", "venue_calendar_not_attested"}
            ):
                raise ValueError("contradictory complete history reasons")
        elif self.calculation_basis != "available_window":
            raise ValueError("unqualified available history")
        elif "covered_window_matches_requested_window" in reasons:
            raise ValueError("contradictory available history reasons")
        elif self.effective_start_date is None:
            if self.status != "unknown" or "no_observations_in_requested_window" not in reasons:
                raise ValueError("unqualified absent observations")
        elif not self.missing_required_observation_count or not reasons & gaps:
            raise ValueError("unqualified missing observations")
        if (
            self.effective_start_date is not None
            and "no_observations_in_requested_window" in reasons
        ):
            raise ValueError("contradictory observed history")
        if "venue_calendar_not_attested" in reasons and (
            self.status != "unknown" or self.calendar_basis != "business_weekdays"
        ):
            raise ValueError("contradictory calendar qualification")
        # Covered observations may lie outside the request. Complete history may
        # also use an explicit beginning-value baseline or excluded dates. Neither
        # requires effective bounds to equal the requested calendar endpoints.


class PerformanceHistoryQualification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["complete", "partial", "unknown", "missing", "invalid", "mismatched"]
    coverage_scope: Literal["calculation_union_window"] = "calculation_union_window"
    source_service: Literal["lotus-performance"] = "lotus-performance"
    source_portfolio_id: str | None = None
    source_calculation_id: str | None = None
    source_input_mode: Annotated[str, Field(pattern="^(stateful|stateless)$")] | None = None
    source_supportability_state: str | None = None
    source_supportability_reason: str | None = None
    source_freshness_bucket: str | None = None
    returned_periods: list[HistoryPeriod] = Field(
        default_factory=list, max_length=MAX_HISTORY_PERIODS
    )
    requested_periods: list[HistoryPeriod] = Field(
        default_factory=list, max_length=MAX_HISTORY_PERIODS
    )
    requested_calendar_basis: Literal["natural_days", "business_weekdays"] | None = None
    return_basis: list[Literal["NET_TWR", "GROSS_TWR"]] = Field(default_factory=list)
    period_return_bases: dict[HistoryPeriod, list[Literal["NET_TWR", "GROSS_TWR"]]] = Field(
        default_factory=dict,
        max_length=MAX_HISTORY_PERIODS,
        description="Returned period/basis identities; each refers to the union evidence only.",
    )
    coverage: PerformanceHistoryCoverage | None = None
    reason_code: str | None = None
    client_publication_allowed: bool = False


class PortfolioReviewPerformance(BaseModel):
    """Additive typed qualification beside the existing performance projections."""

    model_config = ConfigDict(extra="allow")
    history_qualification: PerformanceHistoryQualification | None = Field(
        default=None,
        description=(
            "Source-owned calculation union-window history, bound to source identity and "
            "returned TWR bases. Missing/invalid evidence never attests requested history. "
            "Union missing counts must not be attributed independently to each period."
        ),
    )
