"""Versioned captured return products and precise source-owned cell authority."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from re import fullmatch
from typing import Any, Literal

from pydantic import Field, model_validator

from app.composite_reporting.admission import CompositeEvidenceRefused, admit_composite_response
from app.composite_reporting.eligibility_contract import CompositeEligibilityReportData
from app.composite_reporting.linked_contract import CompositeLinkedReportData
from app.composite_reporting.models import (
    CompositeReportSelection,
    Digest,
    ReturnProductSelection,
    require_product_scope,
)
from app.composite_reporting.projection import require_complete_projection
from app.composite_reporting.semantic_contract import (
    CompositeReportData,
    composite_report_schema,
    require_primary_evidence,
)
from app.composite_reporting.table_builder import build_composite_tables
from app.composite_reporting.table_contract import (
    CompositeCell,
    CompositeColumn,
    CompositeRow,
    CompositeTable,
    TableModel,
    require_complete_table,
    resolve_source_pointer,
    validate_cell_lineage,
)


class CapturedReturnProduct(TableModel):
    pin: ReturnProductSelection
    endpoint: Literal["/composites/twr"]
    method: Literal["POST"]
    source_response_digest: Digest
    source_response: dict[str, Any]


class ProductCell(CompositeCell):
    source_pointer: str = Field(
        pattern=r"^/(source_response|selection|report_facts|source_products)/"
    )


class ProductColumn(CompositeColumn):
    column_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)


class ProductRow(TableModel):
    row_id: str = Field(min_length=1, max_length=256)
    cells: dict[str, ProductCell]


class ProductTable(TableModel):
    table_id: str = Field(min_length=1, max_length=31)
    title: str = Field(min_length=1, max_length=256)
    columns: list[ProductColumn] = Field(min_length=1, max_length=32)
    rows: list[ProductRow] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def require_complete_rows(self) -> ProductTable:
        require_complete_table(
            [column.column_id for column in self.columns],
            [(row.row_id, set(row.cells)) for row in self.rows],
        )
        return self


class CompositeProductReportData(TableModel):
    contract_version: Literal["composite_review.v2"]
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    tenant_id: str
    selection: CompositeReportSelection
    source_response_digest: Digest
    source_response: dict[str, Any]
    report_facts: dict[str, Any]
    source_products: list[CapturedReturnProduct] = Field(min_length=1, max_length=8)
    tables: list[ProductTable] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_pinned_products_and_cells(self) -> CompositeProductReportData:
        require_product_scope(self.selection, [product.pin for product in self.source_products])
        for product in self.source_products:
            admitted = admit_composite_response(
                selection=product.pin.selection,
                admitted_tenant_id=self.tenant_id,
                status_code=200,
                payload=product.source_response,
            )
            if admitted["source_response_digest"] != product.source_response_digest:
                raise CompositeEvidenceRefused("COMPOSITE_PRODUCT_RESPONSE_DIGEST_CONFLICT")
        raw = self.model_dump(mode="json")
        primary = {
            key: raw[key]
            for key in (
                "qualification",
                "publication_state",
                "tenant_id",
                "selection",
                "source_response_digest",
                "source_response",
                "report_facts",
            )
        }
        require_primary_evidence(primary)
        validate_product_tables(raw, self.tables)
        require_product_table_layout(self.selection, self.source_products, self.tables)
        from app.composite_reporting.product_tables import project_product_tables

        expected = project_product_tables(build_composite_tables(primary), self.source_products)
        require_complete_projection(raw, expected)
        return self


def validate_composite_dataset(
    payload: dict[str, Any],
) -> (
    CompositeReportData
    | CompositeProductReportData
    | CompositeLinkedReportData
    | CompositeEligibilityReportData
):
    if payload.get("contract_version") == "composite_review.v4":
        return CompositeEligibilityReportData.model_validate(payload)
    if payload.get("contract_version") == "composite_review.v3":
        return CompositeLinkedReportData.model_validate(payload)
    if payload.get("contract_version") == "composite_review.v2":
        return CompositeProductReportData.model_validate(payload)
    return CompositeReportData.model_validate(payload)


def validate_product_tables(dataset: dict[str, Any], tables: list[ProductTable]) -> None:
    if len({table.table_id for table in tables}) != len(tables):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_TABLE_DUPLICATED")
    for table in tables:
        if all(
            not cell.source_pointer.startswith("/source_products/")
            for row in table.rows
            for cell in row.cells.values()
        ):
            validate_cell_lineage(dataset, [CompositeTable.model_validate(table.model_dump())])
            continue
        for row in table.rows:
            for column in table.columns:
                cell = row.cells[column.column_id]
                if not cell.source_pointer.startswith("/source_products/"):
                    # Mixed-source tables preserve the exact legacy unit guard.
                    validate_cell_lineage(
                        dataset,
                        [
                            CompositeTable(
                                table_id=table.table_id,
                                title=table.title,
                                columns=[column],
                                rows=[
                                    CompositeRow(
                                        row_id=row.row_id,
                                        cells={
                                            column.column_id: CompositeCell.model_validate(
                                                cell.model_dump()
                                            )
                                        },
                                    )
                                ],
                            )
                        ],
                    )
                    continue
                index = r"(?:0|[1-9][0-9]*)"
                financial = fullmatch(
                    rf"/source_products/{index}/source_response/cumulative_return",
                    cell.source_pointer,
                )
                text = fullmatch(
                    rf"/source_products/{index}/(?:source_response/(?:status|methodology)|"
                    rf"source_response_digest|pin/(?:kind|product_key|selection/(?:period_start|"
                    rf"period_end|return_view|reporting_currency|engine_version)))",
                    cell.source_pointer,
                )
                if financial:
                    valid = (
                        column.value_type == "DECIMAL_RETURN" and column.display_unit == "PERCENT"
                    )
                else:
                    valid = text is not None and column.value_type == "TEXT"
                if not valid:
                    raise CompositeEvidenceRefused("COMPOSITE_PRODUCT_CELL_SOURCE_UNIT_CONFLICT")
                source = resolve_source_pointer(dataset, cell.source_pointer)
                if isinstance(source, bool) or not (
                    source is None or isinstance(source, (str, int))
                ):
                    raise CompositeEvidenceRefused("COMPOSITE_REPORT_CELL_SOURCE_INVALID")
                if cell.canonical_value != (None if source is None else str(source)):
                    raise CompositeEvidenceRefused("COMPOSITE_REPORT_CELL_VALUE_MISMATCH")


RETURN_PRODUCT_LABELS = (
    ("product", "Product key"),
    ("kind", "Horizon kind"),
    ("period_start", "Inclusive start"),
    ("period_end", "Inclusive end"),
    ("return_view", "Fee view"),
    ("currency", "Currency"),
    ("return", "Source horizon return"),
    ("status", "Source status"),
    ("methodology", "Source methodology"),
    ("engine_version", "Source engine"),
    ("response_digest", "Captured response digest"),
)


def product_cell_pointers(index: int) -> dict[str, str]:
    root = f"/source_products/{index}"
    return {
        "product": f"{root}/pin/product_key",
        "kind": f"{root}/pin/kind",
        **{
            key: f"{root}/pin/selection/{key}"
            for key in (
                "period_start",
                "period_end",
                "return_view",
                "engine_version",
            )
        },
        "currency": f"{root}/pin/selection/reporting_currency",
        "return": f"{root}/source_response/cumulative_return",
        "status": f"{root}/source_response/status",
        "methodology": f"{root}/source_response/methodology",
        "response_digest": f"{root}/source_response_digest",
    }


def require_product_table_layout(
    selection: CompositeReportSelection,
    products: list[CapturedReturnProduct],
    tables: list[ProductTable],
) -> None:
    monthly = all(
        window.period_start.day == 1
        and (window.period_start.year, window.period_start.month)
        == (
            window.period_end.year,
            window.period_end.month,
        )
        and (window.period_end + timedelta(days=1)).day == 1
        for window in selection.windows
    )
    expected = {
        "Summary",
        "MonthlyReturns" if monthly else "PeriodReturns",
        "Contribution",
        "Methods",
        "Lineage",
        "Disclosures",
        "AnnualReturns",
        "Risk",
        "Members",
        "EligibilityReasons",
        "MembershipHistory",
        "Attribution",
        "Restatement",
    }
    if any(product.pin.kind == "TRAILING_RETURN" for product in products):
        expected.add("TrailingReturns")
    if {table.table_id for table in tables} != expected:
        raise CompositeEvidenceRefused("COMPOSITE_PRODUCT_TABLE_LAYOUT_CONFLICT")
    for kind, table_id in (
        ("CALENDAR_RETURN", "AnnualReturns"),
        ("TRAILING_RETURN", "TrailingReturns"),
    ):
        matching = [(i, p) for i, p in enumerate(products) if p.pin.kind == kind]
        if not matching:
            if kind == "CALENDAR_RETURN":
                _require_uncaptured_calendar(
                    next(table for table in tables if table.table_id == table_id)
                )
            continue
        table = next(table for table in tables if table.table_id == table_id)
        _require_return_table(table, matching)


def _require_return_table(
    table: ProductTable, matching: list[tuple[int, CapturedReturnProduct]]
) -> None:
    labels = [(column.column_id, column.label) for column in table.columns]
    if (
        table.title != table.table_id
        or labels != list(RETURN_PRODUCT_LABELS)
        or [row.row_id for row in table.rows]
        != [product.pin.product_key for _, product in matching]
    ):
        raise CompositeEvidenceRefused("COMPOSITE_PRODUCT_TABLE_LAYOUT_CONFLICT")
    return_column = next(column for column in table.columns if column.column_id == "return")
    if return_column.display_decimal_places != 2:
        raise CompositeEvidenceRefused("COMPOSITE_PRODUCT_CELL_SOURCE_UNIT_CONFLICT")
    for row, (index, _) in zip(table.rows, matching, strict=True):
        pointers = {key: cell.source_pointer for key, cell in row.cells.items()}
        if pointers != product_cell_pointers(index):
            raise CompositeEvidenceRefused("COMPOSITE_PRODUCT_TABLE_LAYOUT_CONFLICT")


def _require_uncaptured_calendar(table: ProductTable) -> None:
    keys = ["metric", "value", "reason_code"]
    if [column.column_id for column in table.columns] != keys or [
        row.row_id for row in table.rows
    ] != ["unavailable"]:
        raise CompositeEvidenceRefused("COMPOSITE_PRODUCT_TABLE_LAYOUT_CONFLICT")
    pointers = {key: cell.source_pointer for key, cell in table.rows[0].cells.items()}
    if pointers != {key: f"/report_facts/uncaptured/AnnualReturns/{key}" for key in keys}:
        raise CompositeEvidenceRefused("COMPOSITE_PRODUCT_TABLE_LAYOUT_CONFLICT")


def composite_product_report_schema() -> dict[str, Any]:
    schema = deepcopy(CompositeProductReportData.model_json_schema())
    schema["$defs"].update(composite_report_schema()["$defs"])
    source = {"$ref": "#/$defs/CompositeCalculatedResponse"}
    schema["properties"]["source_response"] = source
    schema["$defs"]["CapturedReturnProduct"]["properties"]["source_response"] = source
    return schema
