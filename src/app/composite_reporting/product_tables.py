"""Present retained calendar and trailing responses; perform no return calculation."""

from copy import deepcopy
from typing import Any

from app.composite_reporting.product_contract import (
    RETURN_PRODUCT_LABELS,
    CapturedReturnProduct,
    ProductCell,
    ProductColumn,
    ProductRow,
    ProductTable,
    product_cell_pointers,
    validate_composite_dataset,
)


def build_product_tables(
    primary: dict[str, Any], products: list[CapturedReturnProduct]
) -> dict[str, Any]:
    dataset = deepcopy(primary)
    dataset["contract_version"] = "composite_review.v2"
    dataset["source_products"] = [product.model_dump(mode="json") for product in products]
    for kind, table_id in (
        ("CALENDAR_RETURN", "AnnualReturns"),
        ("TRAILING_RETURN", "TrailingReturns"),
    ):
        rows = [
            _product_row(index, product)
            for index, product in enumerate(products)
            if product.pin.kind == kind
        ]
        if not rows:
            continue
        if kind == "CALENDAR_RETURN":
            dataset["report_facts"]["uncaptured"].pop("AnnualReturns", None)
        dataset["tables"] = [table for table in dataset["tables"] if table["table_id"] != table_id]
        dataset["tables"].append(
            ProductTable(
                table_id=table_id,
                title=table_id,
                columns=[_column(key, label) for key, label in RETURN_PRODUCT_LABELS],
                rows=rows,
            ).model_dump(mode="json")
        )
    validate_composite_dataset(dataset)
    return dataset


def _column(key: str, label: str) -> ProductColumn:
    financial = key == "return"
    return ProductColumn(
        column_id=key,
        label=label,
        value_type="DECIMAL_RETURN" if financial else "TEXT",
        unit="DECIMAL_RATIO" if financial else "TEXT",
        display_unit="PERCENT" if financial else "TEXT",
        display_conversion="RATIO_TO_PERCENT_DISPLAY" if financial else "IDENTITY",
        display_decimal_places=2 if financial else None,
        currency=None,
    )


def _product_row(index: int, product: CapturedReturnProduct) -> ProductRow:
    pointers = product_cell_pointers(index)
    values = {
        **product.pin.selection.model_dump(mode="json"),
        "product": product.pin.product_key,
        "kind": product.pin.kind,
        "currency": product.pin.selection.reporting_currency,
        "return": product.source_response["cumulative_return"],
        "status": product.source_response["status"],
        "methodology": product.source_response["methodology"],
        "response_digest": product.source_response_digest,
    }
    return ProductRow(
        row_id=product.pin.product_key,
        cells={
            key: ProductCell(
                canonical_value=values[key],
                availability="AVAILABLE" if values[key] is not None else "UNAVAILABLE",
                reason_codes=[]
                if values[key] is not None
                else (product.source_response["reason_codes"] or ["SOURCE_VALUE_UNAVAILABLE"]),
                source_pointer=pointer,
            )
            for key, pointer in pointers.items()
        },
    )
