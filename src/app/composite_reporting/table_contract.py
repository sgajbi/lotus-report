"""Semantic workbook cells bind exact evidence, units and display policy."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from re import fullmatch
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.composite_reporting.admission import CompositeEvidenceRefused


class TableModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompositeColumn(TableModel):
    column_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value_type: Literal["TEXT", "DECIMAL_RETURN", "MONEY", "COUNT"]
    unit: Literal["TEXT", "DECIMAL_RATIO", "CURRENCY_UNITS", "PORTFOLIO_COUNT"]
    display_unit: Literal[
        "TEXT", "PERCENT", "PERCENTAGE_POINTS", "CURRENCY_UNITS", "PORTFOLIO_COUNT"
    ]
    display_conversion: Literal["IDENTITY", "RATIO_TO_PERCENT_DISPLAY"]
    display_decimal_places: int | None = Field(ge=0, le=12)
    display_rounding_mode: Literal["HALF_UP"] = "HALF_UP"
    currency: str | None = Field(pattern=r"^[A-Z]{3}$")
    scale: Literal["1"] = "1"

    @model_validator(mode="after")
    def require_units(self) -> CompositeColumn:
        if self.value_type == "DECIMAL_RETURN":
            valid = (
                self.unit == "DECIMAL_RATIO"
                and self.display_unit in {"PERCENT", "PERCENTAGE_POINTS"}
                and self.display_conversion == "RATIO_TO_PERCENT_DISPLAY"
                and self.display_decimal_places is not None
                and self.currency is None
            )
        else:
            expected = {"TEXT": "TEXT", "MONEY": "CURRENCY_UNITS", "COUNT": "PORTFOLIO_COUNT"}
            valid = (
                self.unit == expected[self.value_type]
                and self.display_unit == self.unit
                and self.display_conversion == "IDENTITY"
                and ((self.currency is not None) == (self.value_type == "MONEY"))
            )
        if not valid:
            raise ValueError("Cell type, units, currency and display conversion conflict")
        return self


class CompositeCell(TableModel):
    canonical_value: str | None
    availability: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN", "NOT_APPLICABLE"]
    reason_codes: list[str] = Field(max_length=32)
    source_pointer: str = Field(pattern=r"^/(source_response|selection|report_facts)/")

    @model_validator(mode="after")
    def require_availability(self) -> CompositeCell:
        if self.availability == "AVAILABLE":
            if self.canonical_value is None:
                raise ValueError("Available cells require a canonical value")
        elif self.canonical_value is not None or not self.reason_codes:
            raise ValueError("Unavailable cells require null and an explicit reason")
        if any(
            not code or len(code) > 128 or not code.replace("_", "").isalnum()
            for code in self.reason_codes
        ):
            raise ValueError("Reasons require bounded machine codes")
        return self


class CompositeRow(TableModel):
    row_id: str = Field(min_length=1)
    cells: dict[str, CompositeCell]


class CompositeTable(TableModel):
    table_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    columns: list[CompositeColumn] = Field(min_length=1)
    rows: list[CompositeRow] = Field(min_length=1)

    @model_validator(mode="after")
    def require_complete_rows(self) -> CompositeTable:
        keys = [column.column_id for column in self.columns]
        require_complete_table(keys, [(row.row_id, set(row.cells)) for row in self.rows])
        return self


def require_complete_table(keys: list[str], rows: list[tuple[str, set[str]]]) -> None:
    if len(keys) != len(set(keys)):
        raise ValueError("Table columns must be unique")
    if len({row_id for row_id, _ in rows}) != len(rows):
        raise ValueError("Table row identities must be unique")
    if any(cells != set(keys) for _, cells in rows):
        raise ValueError("Every row must account for every declared column")


def resolve_source_pointer(dataset: dict[str, Any], pointer: str) -> Any:
    current: Any = dataset
    try:
        for part in pointer.split("/")[1:]:
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                if not key.isdigit() or str(int(key)) != key:
                    raise KeyError(key)
                current = current[int(key)]
            elif isinstance(current, dict):
                current = current[key]
            else:
                raise KeyError(key)
    except (KeyError, IndexError, ValueError) as exc:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_CELL_POINTER_INVALID") from exc
    return current


def validate_cell_lineage(dataset: dict[str, Any], tables: list[CompositeTable]) -> None:
    if len({table.table_id for table in tables}) != len(tables):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_TABLE_DUPLICATED")
    for table in tables:
        for row in table.rows:
            for column in table.columns:
                cell = row.cells[column.column_id]
                if column.value_type != "TEXT" and not cell.source_pointer.startswith(
                    "/source_response/"
                ):
                    raise CompositeEvidenceRefused("COMPOSITE_REPORT_FINANCIAL_POINTER_INVALID")
                source = resolve_source_pointer(dataset, cell.source_pointer)
                _require_source_units(dataset, column, cell)
                if isinstance(source, bool) or not (
                    source is None or isinstance(source, (str, int))
                ):
                    raise CompositeEvidenceRefused("COMPOSITE_REPORT_CELL_SOURCE_INVALID")
                expected = None if source is None else str(source)
                if cell.canonical_value != expected:
                    raise CompositeEvidenceRefused("COMPOSITE_REPORT_CELL_VALUE_MISMATCH")
                _require_cell_value(column, cell)


def _require_source_units(
    dataset: dict[str, Any], column: CompositeColumn, cell: CompositeCell
) -> None:
    field = cell.source_pointer.rsplit("/", 1)[-1]
    expected = {
        "return_value": ("DECIMAL_RETURN", "PERCENT"),
        "cumulative_return": ("DECIMAL_RETURN", "PERCENT"),
        "dispersion_equal_weight": ("DECIMAL_RETURN", "PERCENT"),
        "beginning_asset_weight": ("DECIMAL_RETURN", "PERCENT"),
        "contribution": ("DECIMAL_RETURN", "PERCENTAGE_POINTS"),
        "beginning_market_value": ("MONEY", "CURRENCY_UNITS"),
        "ending_market_value": ("MONEY", "CURRENCY_UNITS"),
        "member_count": ("COUNT", "PORTFOLIO_COUNT"),
        "excluded_member_count": ("COUNT", "PORTFOLIO_COUNT"),
    }
    index = r"(?:0|[1-9][0-9]*)"
    period_fields = (
        "return_value|cumulative_return|dispersion_equal_weight|beginning_market_value|"
        "ending_market_value|member_count|excluded_member_count"
    )
    member_fields = "return_value|beginning_market_value|beginning_asset_weight|contribution"
    permitted = (
        fullmatch(
            rf"/source_response/(?:cumulative_return|periods/{index}/(?:{period_fields}|"
            rf"member_contributions/{index}/(?:{member_fields})))",
            cell.source_pointer,
        )
        is not None
    )
    # Additive supplier fields do not earn financial authority by borrowing a
    # known basename. Nor may a known financial field be relabelled TEXT to
    # evade its unit/rounding contract.
    if not permitted and column.value_type == "TEXT" and field not in expected:
        return
    if not permitted or expected.get(field) != (column.value_type, column.display_unit):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_CELL_SOURCE_UNIT_CONFLICT")
    if (
        column.value_type == "MONEY"
        and column.currency != dataset["selection"]["reporting_currency"]
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_CELL_CURRENCY_CONFLICT")


def _require_cell_value(column: CompositeColumn, cell: CompositeCell) -> None:
    if cell.canonical_value is None or column.value_type == "TEXT":
        return
    try:
        value = Decimal(cell.canonical_value)
        if not value.is_finite():
            raise ValueError("Nonfinite cell")
        if column.value_type == "COUNT" and (value < 0 or value != value.to_integral_value()):
            raise ValueError("Invalid count")
    except (ValueError, InvalidOperation) as exc:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_CELL_NUMBER_INVALID") from exc
