"""Admit one exact source-owned linked analysis into the existing report family."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.models import (
    CompositeSelectionManifest,
    Digest,
    FeeView,
    Identifier,
    LinkedAnalysisSelection,
    SourceModel,
    SourceNumber,
)
from app.composite_reporting.table_contract import (
    CompositeRow,
    TableModel,
    require_complete_table,
)


class LinkedSourceAuthority(SourceModel):
    source_kind: Literal["INTERNAL", "EXTERNAL_PROVIDER", "HYBRID"]
    return_source_kind: Literal["LOTUS_PERFORMANCE", "EXTERNAL_PROVIDER"]
    provider_id: Identifier
    source_member_id: Identifier
    product_name: Identifier
    product_version: Identifier
    source_revision: Identifier
    source_digest: Digest


class LinkedPeriod(SourceModel):
    portfolio_id: Identifier
    period_start: date
    period_end: date
    return_value: SourceNumber
    beginning_market_value: SourceNumber
    weight: SourceNumber
    contribution: SourceNumber
    linking_factor: SourceNumber
    linked_contribution: SourceNumber
    source_snapshot_id: Identifier
    source_fingerprint: Identifier
    calculation_id: str | None
    restatement_version: Identifier
    restatement_sequence: int = Field(ge=1, strict=True)
    source_authority_identity: LinkedSourceAuthority | None


class LinkedMember(SourceModel):
    portfolio_id: Identifier
    linked_contribution: SourceNumber
    participating_period_count: int = Field(ge=1, strict=True)


class LinkedAnalysisResponse(SourceModel):
    metric_id: Literal["LINKED_MEMBER_CONTRIBUTION"]
    method: Literal["CARINO:v1"]
    calculation_id: UUID
    composite_id: Identifier
    period_start: date
    period_end: date
    return_view: FeeView
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: Literal["CALCULATED_ANALYSIS"]
    qualification: Literal["RETAINED_SOURCE_ATTESTATION_NOT_LIVE_QUALIFIED"]
    units: Literal["DECIMAL_RETURN"]
    constituent_decomposition: Literal["AVAILABLE"]
    cumulative_return: SourceNumber
    total_linked_contribution: SourceNumber
    reconciliation_difference: SourceNumber
    display_rounding_difference: SourceNumber
    members: list[LinkedMember] = Field(min_length=1)
    periods: list[LinkedPeriod] = Field(min_length=1)
    selection_manifest: CompositeSelectionManifest


class LinkedColumn(TableModel):
    column_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    value_type: Literal["TEXT", "DECIMAL_RETURN", "DECIMAL_FACTOR", "MONEY", "COUNT"]
    unit: Literal["TEXT", "DECIMAL_RATIO", "CURRENCY_UNITS", "PERIOD_COUNT"]
    display_unit: Literal[
        "TEXT", "PERCENT", "PERCENTAGE_POINTS", "DECIMAL_RATIO", "CURRENCY_UNITS", "PERIOD_COUNT"
    ]
    display_conversion: Literal["IDENTITY", "RATIO_TO_PERCENT_DISPLAY"]
    display_decimal_places: int | None = Field(ge=0, le=12)
    display_rounding_mode: Literal["HALF_UP"]
    currency: str | None = Field(pattern=r"^[A-Z]{3}$")
    scale: Literal["1"]


class LinkedTable(TableModel):
    table_id: str = Field(min_length=1, max_length=31)
    title: str = Field(min_length=1, max_length=256)
    columns: list[LinkedColumn] = Field(min_length=1, max_length=32)
    rows: list[CompositeRow] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def require_complete_rows(self) -> LinkedTable:
        require_complete_table(
            [column.column_id for column in self.columns],
            [(row.row_id, set(row.cells)) for row in self.rows],
        )
        return self


def admit_linked_response(
    *,
    selection: LinkedAnalysisSelection,
    admitted_tenant_id: str,
    status_code: int,
    payload: dict[str, Any],
) -> None:
    if selection.tenant_id != admitted_tenant_id:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_TENANT_MISMATCH")
    if status_code != 200:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_SOURCE_UNAVAILABLE")
    if response_digest(payload) != selection.response_digest:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_RESPONSE_CHANGED")
    source = LinkedAnalysisResponse.model_validate(payload)
    _require_selected_scope(selection, source)
    _require_member_population(selection, source)


def _require_selected_scope(
    selection: LinkedAnalysisSelection, source: LinkedAnalysisResponse
) -> None:
    request = selection.source_request
    fields = (
        "metric_id",
        "method",
        "calculation_id",
        "composite_id",
        "period_start",
        "period_end",
        "return_view",
        "reporting_currency",
    )
    if any(getattr(request, key) != getattr(source, key) for key in fields):
        raise CompositeEvidenceRefused("COMPOSITE_LINKED_SOURCE_SCOPE_CONFLICT")
    manifest = source.selection_manifest
    if (
        manifest.windows != selection.windows
        or manifest.engine_version != selection.engine_version
        or manifest.calculation_fingerprint != selection.calculation_fingerprint
    ):
        raise CompositeEvidenceRefused("COMPOSITE_LINKED_SOURCE_VECTOR_CONFLICT")


def _require_member_population(
    selection: LinkedAnalysisSelection, source: LinkedAnalysisResponse
) -> None:
    windows = {(window.period_start, window.period_end): window for window in selection.windows}
    keys = [(row.portfolio_id, row.period_start, row.period_end) for row in source.periods]
    counts = Counter(row.portfolio_id for row in source.periods)
    members = {row.portfolio_id: row.participating_period_count for row in source.members}
    if (
        len(keys) != len(set(keys))
        or len(members) != len(source.members)
        or members != dict(counts)
        or {(row.period_start, row.period_end) for row in source.periods} != set(windows)
    ):
        raise CompositeEvidenceRefused("COMPOSITE_LINKED_POPULATION_CONFLICT")
    for row in source.periods:
        window = windows[(row.period_start, row.period_end)]
        if (
            row.restatement_version != str(window.materialization_id)
            or row.restatement_sequence != window.restatement_sequence
        ):
            raise CompositeEvidenceRefused("COMPOSITE_LINKED_MEMBER_VERSION_CONFLICT")


class CompositeLinkedReportData(TableModel):
    contract_version: Literal["composite_review.v3"]
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    tenant_id: str
    selection: LinkedAnalysisSelection
    source_response_digest: Digest
    source_response: dict[str, Any]
    report_facts: dict[str, Any]
    tables: list[LinkedTable] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_complete_source_and_layout(self) -> CompositeLinkedReportData:
        from app.composite_reporting.linked_tables import linked_report_facts, linked_tables

        admit_linked_response(
            selection=self.selection,
            admitted_tenant_id=self.tenant_id,
            status_code=200,
            payload=self.source_response,
        )
        if self.source_response_digest != self.selection.response_digest:
            raise CompositeEvidenceRefused("COMPOSITE_REPORT_DATASET_DIGEST_CONFLICT")
        raw = self.model_dump(mode="json")
        # Reconstruct the COMPLETE source projection, not a projection of the
        # supplied tables. This refuses missing/extra/duplicate columns, rows,
        # tables, policies, pointers and cell values as well as false availability.
        if self.report_facts != linked_report_facts() or raw["tables"] != linked_tables(raw):
            raise CompositeEvidenceRefused("COMPOSITE_LINKED_TABLE_LAYOUT_CONFLICT")
        return self


def composite_linked_report_schema() -> dict[str, Any]:
    schema = CompositeLinkedReportData.model_json_schema()
    source = LinkedAnalysisResponse.model_json_schema()
    definitions = schema.setdefault("$defs", {})
    definitions.update(source.pop("$defs", {}))
    definitions["LinkedAnalysisResponse"] = source
    schema["properties"]["source_response"] = {"$ref": "#/$defs/LinkedAnalysisResponse"}
    return schema
