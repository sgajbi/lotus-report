"""Immutable definition/vector custody for composite SI; no return calculation."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import quote

from pydantic import Field, model_validator

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.eligibility_admission import require_hash
from app.composite_reporting.membership_history import CapturedMembership
from app.composite_reporting.models import (
    CompositeDefinitionPin,
    CompositeReportSelection,
    CompositeSourceContextSelection,
    Digest,
    Identifier,
    SourceModel,
)
from app.composite_reporting.product_contract import CapturedReturnProduct
from app.composite_reporting.projection import require_complete_projection
from app.composite_reporting.semantic_contract import (
    composite_report_schema,
    require_primary_evidence,
)
from app.composite_reporting.table_builder import build_composite_tables
from app.composite_reporting.table_contract import (
    CompositeCell,
    CompositeColumn,
    CompositeTable,
    TableModel,
    require_complete_table,
    validate_cell_lineage,
)


class InceptionDefinition(SourceModel):
    product_name: Literal["CompositeDefinition"]
    product_version: Literal["v1"]
    tenant_id: Identifier
    composite_id: Identifier
    definition_version: Identifier
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    inception_date: date
    termination_date: date | None
    content_hash: Digest
    created_at: datetime


class CapturedInceptionDefinition(TableModel):
    pin: CompositeDefinitionPin
    endpoint: str
    method: Literal["GET"]
    source_response_digest: Digest
    source_response: dict[str, Any]


def definition_endpoint(selection: CompositeReportSelection, pin: CompositeDefinitionPin) -> str:
    return (
        "/api/v1/rebalance/composites/"
        f"{quote(selection.composite_id, safe='')}/definitions/"
        f"{quote(pin.definition_version, safe='')}"
    )


def require_definition(
    selection: CompositeReportSelection, definition: CapturedInceptionDefinition
) -> InceptionDefinition:
    raw = definition.source_response
    typed = InceptionDefinition.model_validate(raw)
    require_hash(raw, recursive=True)
    if (
        response_digest(raw) != definition.source_response_digest
        or definition.source_response_digest != definition.pin.response_digest
        or typed.content_hash != definition.pin.content_hash
        or typed.definition_version != definition.pin.definition_version
        or definition.endpoint != definition_endpoint(selection, definition.pin)
        or typed.tenant_id != selection.tenant_id
        or typed.composite_id != selection.composite_id
        or typed.reporting_currency != selection.reporting_currency
        or any(window.definition_content_hash != typed.content_hash for window in selection.windows)
    ):
        raise CompositeEvidenceRefused("COMPOSITE_INCEPTION_DEFINITION_CONFLICT")
    if selection.period_start < typed.inception_date or (
        typed.termination_date is not None and selection.period_end > typed.termination_date
    ):
        raise CompositeEvidenceRefused("COMPOSITE_INCEPTION_HORIZON_CONFLICT")
    return typed


class InceptionCell(CompositeCell):
    source_pointer: str = Field(
        pattern=r"^/(source_response|selection|report_facts|source_products|definition|supplied_memberships)/"
    )


class InceptionRow(TableModel):
    row_id: str
    cells: dict[str, InceptionCell]


class InceptionTable(TableModel):
    table_id: str = Field(min_length=1, max_length=31)
    title: str
    columns: list[CompositeColumn] = Field(min_length=1, max_length=32)
    rows: list[InceptionRow] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def require_rows(self) -> InceptionTable:
        require_complete_table(
            [column.column_id for column in self.columns],
            [(row.row_id, set(row.cells)) for row in self.rows],
        )
        return self


class CompositeSourceContextReportData(TableModel):
    contract_version: Literal["composite_review.v8"]
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    tenant_id: Identifier
    selection: CompositeReportSelection
    source_response_digest: Digest
    source_response: dict[str, Any]
    definition: CapturedInceptionDefinition
    source_context: CompositeSourceContextSelection
    source_products: list[CapturedReturnProduct] = Field(
        default_factory=list, max_length=8, exclude_if=lambda value: not value
    )
    supplied_memberships: list[CapturedMembership] = Field(max_length=2)
    report_facts: dict[str, Any]
    tables: list[InceptionTable] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_custody_and_projection(self) -> CompositeSourceContextReportData:
        raw = self.model_dump(mode="json")
        require_primary_evidence(raw)
        require_definition(self.selection, self.definition)
        if (
            self.source_context.definition != self.definition.pin
            or self.source_context.memberships != [item.pin for item in self.supplied_memberships]
        ):
            raise CompositeEvidenceRefused("COMPOSITE_SOURCE_CONTEXT_PIN_CONFLICT")
        primary_tables = [
            CompositeTable.model_validate(table.model_dump(mode="json"))
            for table in self.tables
            if table.table_id not in {"SinceInception", "MembershipHistory"}
            or (table.table_id == "MembershipHistory" and not self.supplied_memberships)
            if not self.source_products
            or table.table_id not in {"AnnualReturns", "TrailingReturns"}
        ]
        validate_cell_lineage(raw, primary_tables)
        # Rebuild the complete deterministic projection: SI cannot borrow an
        # additive source field, forge availability, or omit any primary row.
        require_complete_projection(
            raw,
            project_source_context(
                raw, self.source_context, self.definition, self.supplied_memberships
            ),
        )
        return self


def _inception_column(key: str) -> CompositeColumn:
    financial = key == "return"
    return CompositeColumn(
        column_id=key,
        label=key.replace("_", " ").capitalize(),
        value_type="DECIMAL_RETURN" if financial else "TEXT",
        unit="DECIMAL_RATIO" if financial else "TEXT",
        display_unit="PERCENT" if financial else "TEXT",
        display_conversion="RATIO_TO_PERCENT_DISPLAY" if financial else "IDENTITY",
        display_decimal_places=2 if financial else None,
        currency=None,
    )


def project_inception_tables(
    primary: dict[str, Any], definition: CapturedInceptionDefinition
) -> dict[str, Any]:
    selection = CompositeReportSelection.model_validate(primary["selection"])
    typed = require_definition(selection, definition)
    dataset = build_composite_tables(primary)
    dataset["contract_version"] = "composite_review.v8"
    dataset["definition"] = definition.model_dump(mode="json")
    source = dataset["source_response"]
    reason = (
        "SOURCE_VECTOR_BEGINS_AFTER_PINNED_INCEPTION"
        if selection.period_start != typed.inception_date
        else "COMPLETE_READY_SOURCE_VECTOR_REQUIRED"
        if source["status"] != "READY" or any(p["status"] != "READY" for p in source["periods"])
        else None
    )
    dataset["report_facts"]["since_inception"] = {
        "availability": "UNAVAILABLE" if reason else "AVAILABLE",
        "reason": reason or "NONE",
        "value": None if reason else source["cumulative_return"],
        "coverage_convention": "INCLUSIVE_SOURCE_INCEPTION",
    }
    pointers = {
        "inception_date": "/definition/source_response/inception_date",
        "period_start": "/selection/period_start",
        "period_end": "/selection/period_end",
        "return": "/report_facts/since_inception/value"
        if reason
        else "/source_response/cumulative_return",
        "availability": "/report_facts/since_inception/availability",
        "reason": "/report_facts/since_inception/reason",
        "coverage_convention": "/report_facts/since_inception/coverage_convention",
        "definition_version": "/definition/pin/definition_version",
        "definition_hash": "/definition/pin/content_hash",
        "definition_response_digest": "/definition/source_response_digest",
    }
    from app.composite_reporting.table_contract import resolve_source_pointer

    values = {key: resolve_source_pointer(dataset, pointer) for key, pointer in pointers.items()}
    table = InceptionTable(
        table_id="SinceInception",
        title="Since inception — calculated replay",
        columns=[_inception_column(key) for key in pointers],
        rows=[
            InceptionRow(
                row_id="since_inception",
                cells={
                    key: InceptionCell(
                        canonical_value=None if values[key] is None else str(values[key]),
                        availability="UNAVAILABLE" if key == "return" and reason else "AVAILABLE",
                        reason_codes=[reason] if key == "return" and reason else [],
                        source_pointer=pointer,
                    )
                    for key, pointer in pointers.items()
                },
            )
        ],
    )
    dataset["tables"].append(table.model_dump(mode="json"))
    return dataset


def source_context_report_schema() -> dict[str, Any]:
    schema = CompositeSourceContextReportData.model_json_schema()
    schema["$defs"].update(composite_report_schema()["$defs"])
    schema["properties"]["source_response"] = {"$ref": "#/$defs/CompositeCalculatedResponse"}
    schema["$defs"]["CapturedReturnProduct"]["properties"]["source_response"] = {
        "$ref": "#/$defs/CompositeCalculatedResponse"
    }
    schema["$defs"]["CapturedInceptionDefinition"]["properties"]["source_response"] = {
        "$ref": "#/$defs/InceptionDefinition"
    }
    schema["$defs"]["InceptionDefinition"] = InceptionDefinition.model_json_schema()
    from app.composite_reporting.eligibility_contract import EligibilityMembership

    membership = EligibilityMembership.model_json_schema()
    schema["$defs"].update(membership.pop("$defs", {}))
    schema["$defs"]["EligibilityMembership"] = membership
    schema["$defs"]["CapturedMembership"]["properties"]["source_response"] = {
        "$ref": "#/$defs/EligibilityMembership"
    }
    return schema


def project_source_context(
    primary: dict[str, Any],
    context: CompositeSourceContextSelection,
    definition: CapturedInceptionDefinition,
    memberships: list[CapturedMembership],
) -> dict[str, Any]:
    from app.composite_reporting.membership_history import (
        project_membership_history,
        require_membership_join,
    )

    selection = CompositeReportSelection.model_validate(primary["selection"])
    require_definition(selection, definition)
    products = [
        CapturedReturnProduct.model_validate(item) for item in primary.get("source_products", [])
    ]
    for product in products:
        require_definition(product.pin.selection, definition)
    if (
        sum(
            len(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
            for raw in [
                primary["source_response"],
                *(product.source_response for product in products),
                definition.source_response,
                *(item.source_response for item in memberships),
            ]
        )
        > 8_388_608
    ):
        raise CompositeEvidenceRefused("COMPOSITE_SOURCE_CONTEXT_CAPTURE_CAPACITY_EXCEEDED")
    require_membership_join(
        selection,
        definition.source_response,
        definition.endpoint,
        memberships,
        additional_selections=[product.pin.selection for product in products],
    )
    dataset = (
        project_inception_tables(primary, definition)
        if context.since_inception
        else build_composite_tables(primary)
    )
    dataset["contract_version"] = "composite_review.v8"
    dataset["source_context"] = context.model_dump(mode="json")
    dataset["definition"] = definition.model_dump(mode="json")
    dataset["supplied_memberships"] = [item.model_dump(mode="json") for item in memberships]
    if products:
        from app.composite_reporting.product_tables import (
            build_product_tables,
            project_product_tables,
        )

        # Reuse the complete v2 evidence, coverage, scope and cell admission.
        # Context tables are then compared by the same complete v8 projection.
        primary_evidence = {
            key: primary[key]
            for key in (
                "qualification",
                "publication_state",
                "tenant_id",
                "selection",
                "source_response_digest",
                "source_response",
            )
        }
        build_product_tables(build_composite_tables(primary_evidence), products)
        dataset = project_product_tables(dataset, products)
        dataset["contract_version"] = "composite_review.v8"
    if memberships:
        dataset = project_membership_history(dataset)
    return dataset
