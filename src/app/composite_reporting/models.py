"""Consumed Performance wire contracts and exact caller-selected report identity.

Financial values validate as finite decimals, but the accepted raw response is
retained separately: typed projection must not rewrite source precision or bytes.
Additive producer fields survive custody; report selectors forbid unknown fields.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    WithJsonSchema,
    model_validator,
)

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
    Decimal,
    BeforeValidator(_finite_source_number),
    Field(allow_inf_nan=False),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer"},
                {
                    "type": "string",
                    "pattern": r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$",
                },
            ]
        },
        mode="validation",
    ),
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


class CalendarReturnSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["CALENDAR_RETURN"]
    product_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    year: int = Field(ge=1, le=9999, strict=True)
    selection: CompositeReportSelection

    @model_validator(mode="after")
    def require_calendar_year(self) -> CalendarReturnSelection:
        if (self.selection.period_start, self.selection.period_end) != (
            date(self.year, 1, 1),
            date(self.year, 12, 31),
        ) or len(self.selection.windows) != 12:
            raise ValueError("COMPOSITE_CALENDAR_YEAR_REQUIRED")
        _require_complete_months(self.selection)
        return self


class TrailingReturnSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["TRAILING_RETURN"]
    product_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    months: int = Field(ge=1, le=120, strict=True)
    selection: CompositeReportSelection

    @model_validator(mode="after")
    def require_month_count(self) -> TrailingReturnSelection:
        if len(self.selection.windows) != self.months:
            raise ValueError("COMPOSITE_TRAILING_MONTH_COUNT_REQUIRED")
        _require_complete_months(self.selection)
        return self


ReturnProductSelection = Annotated[
    CalendarReturnSelection | TrailingReturnSelection, Field(discriminator="kind")
]


def _require_complete_months(selection: CompositeReportSelection) -> None:
    for window in selection.windows:
        start, end = window.period_start, window.period_end
        if (
            start.day != 1
            or (start.year, start.month) != (end.year, end.month)
            or end.day != calendar.monthrange(start.year, start.month)[1]
        ):
            raise ValueError("COMPOSITE_PRODUCT_COMPLETE_MONTHS_REQUIRED")


def require_product_scope(
    primary: CompositeReportSelection, products: list[ReturnProductSelection]
) -> None:
    keys = [product.product_key for product in products]
    if len(keys) != len(set(keys)):
        raise ValueError("COMPOSITE_PRODUCT_KEY_DUPLICATED")
    fields = (
        "tenant_id",
        "composite_id",
        "reporting_currency",
        "return_view",
        "methodology",
        "engine_version",
    )
    for product in products:
        selection = product.selection
        if any(getattr(primary, field) != getattr(selection, field) for field in fields):
            raise ValueError("COMPOSITE_PRODUCT_SCOPE_CONFLICT")
        windows = [
            window
            for window in primary.windows
            if selection.period_start <= window.period_start
            and window.period_end <= selection.period_end
        ]
        if windows != selection.windows:
            raise ValueError("COMPOSITE_PRODUCT_PIN_VECTOR_CONFLICT")
        if (
            isinstance(product, TrailingReturnSelection)
            and selection.period_end != primary.period_end
        ):
            raise ValueError("COMPOSITE_TRAILING_AS_OF_CONFLICT")


class LinkedAnalysisRequest(BaseModel):
    """Exact registered Performance operation; no client-supplied economics."""

    model_config = ConfigDict(extra="forbid")

    metric_id: Literal["LINKED_MEMBER_CONTRIBUTION"]
    method: Literal["CARINO:v1"]
    composite_id: Identifier
    calculation_id: UUID
    period_start: date
    period_end: date
    return_view: FeeView
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    materialization_ids: list[UUID] = Field(min_length=1, max_length=120)
    restatement_sequence: None = Field(
        default=None, description="Exact vector selection forbids a competing numeric sequence."
    )


class LinkedAnalysisSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: Identifier
    source_request: LinkedAnalysisRequest
    windows: list[CompositeWindowPin] = Field(min_length=1, max_length=120)
    engine_version: Identifier
    calculation_fingerprint: Digest
    response_digest: Digest

    @model_validator(mode="after")
    def require_exact_vector(self) -> LinkedAnalysisSelection:
        request = self.source_request
        identifiers = [window.materialization_id for window in self.windows]
        if identifiers != request.materialization_ids or len(set(identifiers)) != len(identifiers):
            raise ValueError("COMPOSITE_LINKED_PIN_VECTOR_CONFLICT")
        if (self.windows[0].period_start, self.windows[-1].period_end) != (
            request.period_start,
            request.period_end,
        ):
            raise ValueError("COMPOSITE_LINKED_PERIOD_CONFLICT")
        for previous, current in zip(self.windows, self.windows[1:]):
            if current.period_start != previous.period_end + timedelta(days=1):
                raise ValueError("COMPOSITE_LINKED_WINDOW_GAP")
        return self

    @property
    def composite_id(self) -> str:
        return self.source_request.composite_id

    @property
    def period_start(self) -> date:
        return self.source_request.period_start

    @property
    def period_end(self) -> date:
        return self.source_request.period_end

    @property
    def reporting_currency(self) -> str:
        return self.source_request.reporting_currency

    @property
    def calculation_id(self) -> UUID:
        return self.source_request.calculation_id

    def performance_request(self) -> dict[str, Any]:
        return self.source_request.model_dump(mode="json")


class EvaluatedEligibilityPin(BaseModel):
    """Exact retained proposal; never implies checker approval or publication."""

    model_config = ConfigDict(extra="forbid")
    evidence_kind: Literal["EVALUATED_ONLY"]
    month: str = Field(pattern=r"^[0-9]{4}-(?:0[1-9]|1[0-2])$")
    evaluation_revision: Identifier
    proposal_content_hash: Digest
    proposal_response_digest: Digest
    parent_membership_revision: Identifier
    parent_membership_content_hash: Digest
    source_cut_id: Identifier


class PublishedEligibilityPin(BaseModel):
    """Whole publication custody plus independently pinned canonical products."""

    model_config = ConfigDict(extra="forbid")
    evidence_kind: Literal["PUBLISHED"]
    month: str = Field(pattern=r"^[0-9]{4}-(?:0[1-9]|1[0-2])$")
    evaluation_revision: Identifier
    proposal_content_hash: Digest
    approval_content_hash: Digest
    receipt_content_hash: Digest
    receipt_response_digest: Digest
    membership_revision: Identifier
    membership_content_hash: Digest
    membership_response_digest: Digest
    attestation_version: Identifier
    universe_content_hash: Digest
    universe_response_digest: Digest
    parent_membership_revision: Identifier
    parent_membership_content_hash: Digest
    parent_response_digest: Digest
    publication_sequence: int = Field(gt=0, strict=True)
    publication_response_digest: Digest
    source_cut_id: Identifier


EligibilityMonthPin = Annotated[
    PublishedEligibilityPin | EvaluatedEligibilityPin, Field(discriminator="evidence_kind")
]


class EligibilitySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    period_start: date
    period_end: date
    months: list[EligibilityMonthPin] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_ordered_months(self) -> EligibilitySelection:
        months = [pin.month for pin in self.months]
        if months != sorted(set(months)) or self.period_end < self.period_start:
            raise ValueError("COMPOSITE_ELIGIBILITY_MONTH_VECTOR_INVALID")
        if months[0] != self.period_start.strftime("%Y-%m") or months[-1] != (
            self.period_end.strftime("%Y-%m")
        ):
            raise ValueError("COMPOSITE_ELIGIBILITY_HORIZON_CONFLICT")
        if (
            self.period_start.day != 1
            or self.period_end.day
            != calendar.monthrange(self.period_end.year, self.period_end.month)[1]
        ):
            raise ValueError("COMPOSITE_ELIGIBILITY_COMPLETE_MONTHS_REQUIRED")
        # Noncontiguous selected months remain explicit gaps, never filled forward.
        return self


class MonthlyReceiptPin(BaseModel):
    """Caller-pinned retained monthly receipt; no latest authority inference."""

    model_config = ConfigDict(extra="forbid")
    product_version: Literal["v1", "v2"]
    evaluation_revision: Identifier
    approval_content_hash: Digest
    receipt_content_hash: Digest
    receipt_response_digest: Digest


class AmendmentEvaluatedPin(EvaluatedEligibilityPin):
    lineage_receipts: list[MonthlyReceiptPin] = Field(min_length=1, max_length=31)


class AmendmentPublishedPin(PublishedEligibilityPin):
    lineage_receipts: list[MonthlyReceiptPin] = Field(min_length=1, max_length=31)
    parent_publication_response_digest: Digest


class AmendmentEligibilitySelection(BaseModel):
    """Explicit source-v2 ordinary-month selection; independent definition version."""

    model_config = ConfigDict(extra="forbid")
    selection_version: Literal["v2"]
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    period_start: date
    period_end: date
    months: list[
        Annotated[
            AmendmentPublishedPin | AmendmentEvaluatedPin, Field(discriminator="evidence_kind")
        ]
    ] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_ordered_months(self) -> AmendmentEligibilitySelection:
        plain = self.model_dump(mode="json", exclude={"selection_version"})
        for month in plain["months"]:
            month.pop("lineage_receipts")
            month.pop("parent_publication_response_digest", None)
        EligibilitySelection.model_validate(plain)
        return self


def eligibility_selection_version(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("selection_version", "v1"))
    return str(getattr(value, "selection_version", "v1"))


class HistoricalReceiptPin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_version: Literal["v3", "v4"]
    evaluation_revision: Identifier
    approval_content_hash: Digest
    receipt_content_hash: Digest
    receipt_response_digest: Digest


class HistoricalEvaluatedRootPin(EvaluatedEligibilityPin):
    product_version: Literal["v3"]


class HistoricalPublishedRootPin(PublishedEligibilityPin):
    product_version: Literal["v3"]


class HistoricalEvaluatedCorrectionPin(EvaluatedEligibilityPin):
    product_version: Literal["v4"]
    lineage_receipts: list[HistoricalReceiptPin] = Field(min_length=1, max_length=31)


class HistoricalPublishedCorrectionPin(PublishedEligibilityPin):
    product_version: Literal["v4"]
    lineage_receipts: list[HistoricalReceiptPin] = Field(min_length=1, max_length=31)
    parent_publication_response_digest: Digest


def historical_month_variant(value: Any) -> str:
    if isinstance(value, dict):
        return f"{value.get('product_version')}:{value.get('evidence_kind')}"
    return f"{getattr(value, 'product_version', None)}:{getattr(value, 'evidence_kind', None)}"


HistoricalMonthlyPin = Annotated[
    Annotated[HistoricalEvaluatedRootPin, Tag("v3:EVALUATED_ONLY")]
    | Annotated[HistoricalPublishedRootPin, Tag("v3:PUBLISHED")]
    | Annotated[HistoricalEvaluatedCorrectionPin, Tag("v4:EVALUATED_ONLY")]
    | Annotated[HistoricalPublishedCorrectionPin, Tag("v4:PUBLISHED")],
    Discriminator(historical_month_variant),
]


class HistoricalEligibilitySelection(BaseModel):
    """Exact new monthly authority; never implicitly upgrades a frozen selector."""

    model_config = ConfigDict(extra="forbid")
    selection_version: Literal["v3"]
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    period_start: date
    period_end: date
    months: list[HistoricalMonthlyPin] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_ordered_months(self) -> HistoricalEligibilitySelection:
        plain = self.model_dump(mode="json", exclude={"selection_version"})
        for month in plain["months"]:
            month.pop("product_version")
            receipts = month.pop("lineage_receipts", [])
            month.pop("parent_publication_response_digest", None)
            if receipts and (
                receipts[-1]["product_version"] != "v3"
                or any(row["product_version"] != "v4" for row in receipts[:-1])
            ):
                raise ValueError("Historical corrections require one v3 original root")
        EligibilitySelection.model_validate(plain)
        return self


VersionedEligibilitySelection = Annotated[
    Annotated[EligibilitySelection, Tag("v1")]
    | Annotated[AmendmentEligibilitySelection, Tag("v2")]
    | Annotated[HistoricalEligibilitySelection, Tag("v3")],
    Discriminator(eligibility_selection_version),
]


class PooledSourcePin(BaseModel):
    """Exact retained owner source vector, distinct from read authority."""

    model_config = ConfigDict(extra="forbid")
    pin_id: Identifier
    owner: Identifier
    product_name: Identifier
    product_version: Identifier
    revision: Identifier
    source_cut_id: Identifier
    payload_digest: Digest
    compatibility_group: Identifier
    coverage_from: date
    coverage_to: date
    completeness: Literal["COMPLETE"]
    page_ids: list[Identifier] = Field(min_length=1, max_length=10000)
    expected_page_count: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def require_complete_pages(self) -> PooledSourcePin:
        if self.coverage_to < self.coverage_from or (
            len(self.page_ids) != self.expected_page_count
            or len(set(self.page_ids)) != len(self.page_ids)
        ):
            raise ValueError("COMPOSITE_POOLED_SOURCE_PIN_INCOMPLETE")
        return self


class PooledAnalysisSelection(BaseModel):
    """Read an exact final result; neither submission nor caller economics."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: Identifier
    composite_id: Identifier
    calculation_id: UUID
    schema_version: Literal["composite-pooled-mwr.v1"]
    metric_id: Literal["POOLED_MONEY_WEIGHTED_RETURN"]
    method: Literal["XIRR:v1"]
    period_start: date
    period_end: date
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    return_view: FeeView
    fee_basis: Identifier
    policy_binding_id: Identifier
    policy_content_hash: Identifier
    day_count_basis: Literal["BUS/252", "ACT/365", "ACT/ACT"]
    fallback_policy: Literal["REQUIRE_XIRR", "ALLOW_MODIFIED_DIETZ"]
    engine_version: Identifier
    source_manifest_id: Identifier
    input_manifest_digest: Digest
    source_bundle_digest: Digest
    response_digest: Digest
    expected_portfolio_ids: list[Identifier] = Field(min_length=1, max_length=10000)
    source_pins: list[PooledSourcePin] = Field(min_length=1, max_length=128)
    correction_of_calculation_id: UUID | None
    predecessor_response_digest: Digest | None

    @model_validator(mode="after")
    def require_exact_pooled_identity(self) -> PooledAnalysisSelection:
        if self.period_end <= self.period_start:
            raise ValueError("COMPOSITE_POOLED_POSITIVE_INTERVAL_REQUIRED")
        if len(set(self.expected_portfolio_ids)) != len(self.expected_portfolio_ids):
            raise ValueError("COMPOSITE_POOLED_DUPLICATE_MEMBER")
        if len({pin.pin_id for pin in self.source_pins}) != len(self.source_pins):
            raise ValueError("COMPOSITE_POOLED_DUPLICATE_SOURCE_PIN")
        if (self.correction_of_calculation_id is None) != (
            self.predecessor_response_digest is None
        ) or self.correction_of_calculation_id == self.calculation_id:
            raise ValueError("COMPOSITE_POOLED_CORRECTION_PIN_REQUIRED")
        return self


class CompositeReviewJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: CompositeReportSelection | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Exact retained calculation. Official publication authority is unavailable.",
    )
    linked_selection: LinkedAnalysisSelection | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Exact linked-analysis primary operation; exclusive of TWR return products.",
    )
    eligibility_selection: VersionedEligibilitySelection | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Exact published or evaluated-only monthly eligibility; NOT_ATTESTED.",
    )
    pooled_selection: PooledAnalysisSelection | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Exact retained pooled XIRR outcome and source vector; NOT_ATTESTED.",
    )
    source_products: list[ReturnProductSelection] | None = Field(
        default=None,
        min_length=1,
        max_length=8,
        exclude_if=lambda value: value is None,
        description="Exact calendar/trailing calculations; absent retains the v1 contract.",
    )
    requested_output_formats: list[Literal["json", "xlsx"]] = Field(
        default=["json"],
        min_length=1,
        max_length=1,
        description="Select one internal calculated-review output: JSON dataset or XLSX workbook.",
    )
    options: dict[str, Any] = Field(default_factory=dict, description="Governed retention options.")

    @model_validator(mode="after")
    def require_products(self) -> CompositeReviewJobRequest:
        if "composite_source_products" in self.options:
            raise ValueError("Use the typed source_products field")
        if "composite_linked_selection" in self.options:
            raise ValueError("Use the typed linked_selection field")
        if "composite_eligibility_selection" in self.options:
            raise ValueError("Use the typed eligibility_selection field")
        if "composite_pooled_selection" in self.options:
            raise ValueError("Use the typed pooled_selection field")
        if (
            sum(
                item is not None
                for item in (
                    self.selection,
                    self.linked_selection,
                    self.eligibility_selection,
                    self.pooled_selection,
                )
            )
            != 1
        ):
            raise ValueError("Select exactly one composite primary operation")
        if self.source_products is not None:
            if self.selection is None:
                raise ValueError("Linked analysis cannot be a TWR return product")
            require_product_scope(self.selection, self.source_products)
        return self

    @property
    def primary_selection(
        self,
    ) -> (
        CompositeReportSelection
        | LinkedAnalysisSelection
        | EligibilitySelection
        | AmendmentEligibilitySelection
        | HistoricalEligibilitySelection
        | PooledAnalysisSelection
    ):
        if self.pooled_selection is not None:
            return self.pooled_selection
        if self.eligibility_selection is not None:
            return self.eligibility_selection
        if self.linked_selection is not None:
            return self.linked_selection
        assert self.selection is not None
        return self.selection

    def capture_options(self) -> dict[str, Any]:
        if self.pooled_selection is not None:
            return {
                **self.options,
                "composite_pooled_selection": self.pooled_selection.model_dump(mode="json"),
            }
        if self.eligibility_selection is not None:
            return {
                **self.options,
                "composite_eligibility_selection": self.eligibility_selection.model_dump(
                    mode="json"
                ),
            }
        if self.linked_selection is not None:
            return {
                **self.options,
                "composite_linked_selection": self.linked_selection.model_dump(mode="json"),
            }
        assert self.selection is not None
        options = {**self.options, "composite_selection": self.selection.model_dump(mode="json")}
        if self.source_products is not None:
            options["composite_source_products"] = [
                product.model_dump(mode="json") for product in self.source_products
            ]
        return options
