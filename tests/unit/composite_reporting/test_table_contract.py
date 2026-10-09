from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.composite_reporting.admission import CompositeEvidenceRefused, admit_composite_response
from app.composite_reporting.table_builder import build_composite_tables
from app.composite_reporting.table_contract import (
    CompositeColumn,
    CompositeTable,
    resolve_source_pointer,
    validate_cell_lineage,
)
from tests.unit.composite_reporting.fixtures import calculated_example, selection_for


def example_tables(payload=None):
    payload = payload or calculated_example()
    dataset = admit_composite_response(
        selection=selection_for(payload),
        admitted_tenant_id="tenant-a",
        status_code=200,
        payload=payload,
    )
    return build_composite_tables(dataset)


def test_every_cell_has_exact_retained_provenance_and_financial_units():
    dataset = example_tables()
    tables = [CompositeTable.model_validate(item) for item in dataset["tables"]]
    validate_cell_lineage(dataset, tables)
    for table in tables:
        for row in table.rows:
            for cell in row.cells.values():
                expected = resolve_source_pointer(dataset, cell.source_pointer)
                assert cell.canonical_value == (None if expected is None else str(expected))
    summary = next(table for table in tables if table.table_id == "Summary")
    returns = summary.rows[0].cells["return"]
    assert returns.canonical_value == "0.030200000000"
    monthly = next(table for table in tables if table.table_id == "MonthlyReturns")
    month = monthly.rows[0].cells["return_value"]
    column = next(column for column in monthly.columns if column.column_id == "return_value")
    assert month.canonical_value == "0.010000000000"
    assert column.unit == "DECIMAL_RATIO"
    assert column.display_conversion == "RATIO_TO_PERCENT_DISPLAY"
    # Independent formatting oracle: Excel percent format displays ratio *100;
    # the cell retains .01, so source +1% cannot be sent as numeric 1 (100%).
    assert format(Decimal(month.canonical_value), ".2%") == "1.00%"
    assert column.display_decimal_places == 2
    contribution = next(table for table in tables if table.table_id == "Contribution")
    assert (
        next(
            column for column in contribution.columns if column.column_id == "contribution"
        ).display_unit
        == "PERCENTAGE_POINTS"
    )
    assets = next(
        column for column in monthly.columns if column.column_id == "beginning_market_value"
    )
    assert (assets.unit, assets.currency, assets.scale, assets.display_conversion) == (
        "CURRENCY_UNITS",
        "USD",
        "1",
        "IDENTITY",
    )
    assert dataset["source_response"] == calculated_example()
    assert dataset["report_facts"]["authority"] == {
        "receipt": None,
        "control_revision": None,
        "availability": "UNAVAILABLE",
        "reason_code": "SOURCE_AUTHORITY_NOT_ATTESTED",
    }


def test_unavailable_metrics_and_leading_zero_formula_like_ids_stay_literal():
    dataset = example_tables()
    for table in dataset["tables"]:
        if table["table_id"] == "Contribution":
            assert table["columns"][0]["value_type"] == "TEXT"
            assert (
                table["rows"][0]["cells"]["portfolio_id"]["canonical_value"]
                == "00000000000000000001"
            )
            assert (
                table["rows"][1]["cells"]["portfolio_id"]["canonical_value"]
                == "=literal-identifier"
            )
        if table["table_id"] == "Risk":
            cell = table["rows"][0]["cells"]["value"]
            assert cell["canonical_value"] is None
            assert cell["availability"] == "UNAVAILABLE"
            assert cell["reason_codes"] == ["SOURCE_PRODUCT_NOT_CAPTURED"]


@pytest.mark.parametrize(
    "defect,code",
    [
        ("value", "CELL_VALUE_MISMATCH"),
        ("pointer", "CELL_POINTER_INVALID"),
        ("financial-report-fact", "FINANCIAL_POINTER_INVALID"),
        ("duplicate-table", "TABLE_DUPLICATED"),
    ],
)
def test_table_reconciliation_refuses_forged_cells(defect, code):
    dataset = example_tables()
    tables = [CompositeTable.model_validate(item) for item in dataset["tables"]]
    cell = tables[0].rows[0].cells["return"]
    if defect == "value":
        cell.canonical_value = "999"
    elif defect == "pointer":
        cell.source_pointer = "/source_response/absent"
    elif defect == "financial-report-fact":
        dataset["report_facts"]["invented_return"] = cell.canonical_value
        cell.source_pointer = "/report_facts/invented_return"
    else:
        tables.append(tables[0])
    with pytest.raises(CompositeEvidenceRefused, match=code):
        validate_cell_lineage(dataset, tables)


@pytest.mark.parametrize(
    "defect", ["null-to-zero", "missing-reason", "empty-column", "duplicate-row"]
)
def test_availability_and_table_shape_fail_closed(defect):
    dataset = example_tables()
    table = deepcopy(next(item for item in dataset["tables"] if item["table_id"] == "Risk"))
    if defect == "null-to-zero":
        table["rows"][0]["cells"]["value"]["canonical_value"] = "0"
    elif defect == "missing-reason":
        table["rows"][0]["cells"]["value"]["reason_codes"] = []
    elif defect == "empty-column":
        table["rows"][0]["cells"].pop("metric")
    else:
        table["rows"].append(table["rows"][0])
    with pytest.raises(ValidationError):
        CompositeTable.model_validate(table)


@pytest.mark.parametrize(
    "defect", ["basis-points", "money-as-percent", "ratio-as-identity", "missing-currency"]
)
def test_conflicting_units_cannot_relabel_financial_values(defect):
    dataset = example_tables()
    table = next(item for item in dataset["tables"] if item["table_id"] == "MonthlyReturns")
    column = deepcopy(
        next(item for item in table["columns"] if item["column_id"] == "return_value")
    )
    if defect == "basis-points":
        column["display_unit"] = "BASIS_POINTS"
    elif defect == "money-as-percent":
        column["value_type"] = "MONEY"
    elif defect == "ratio-as-identity":
        column["display_conversion"] = "IDENTITY"
    else:
        column = deepcopy(
            next(item for item in table["columns"] if item["column_id"] == "beginning_market_value")
        )
        column["currency"] = None
    with pytest.raises(ValidationError):
        CompositeColumn.model_validate(column)


def test_partial_intervals_are_not_labelled_as_monthly_returns():
    payload = calculated_example()
    payload["period_start"] = "2026-01-05"
    payload["selection_manifest"]["windows"][0]["period_start"] = "2026-01-05"
    payload["periods"][0]["period_start"] = "2026-01-05"
    for member in payload["periods"][0]["member_contributions"]:
        member["period_start"] = "2026-01-05"
    dataset = example_tables(payload)
    assert "PeriodReturns" in {table["table_id"] for table in dataset["tables"]}
    assert "MonthlyReturns" not in {table["table_id"] for table in dataset["tables"]}


@pytest.mark.parametrize("defect", ["additive-financial", "text-relabelling", "additive-text"])
def test_only_precise_owned_financial_paths_are_authoritative(defect):
    dataset = example_tables()
    tables = [CompositeTable.model_validate(item) for item in dataset["tables"]]
    table = tables[0]
    cell = table.rows[0].cells["return"]
    column = next(item for item in table.columns if item.column_id == "return")
    if defect in {"additive-financial", "additive-text"}:
        dataset["source_response"]["injected"] = {"return_value": cell.canonical_value}
        cell.source_pointer = "/source_response/injected/return_value"
    if defect in {"text-relabelling", "additive-text"}:
        replacement = next(item for item in table.columns if item.column_id == "composite_id")
        index = table.columns.index(column)
        table.columns[index] = replacement.model_copy(update={"column_id": "return"})
    with pytest.raises(CompositeEvidenceRefused, match="CELL_SOURCE_UNIT_CONFLICT"):
        validate_cell_lineage(dataset, tables)
