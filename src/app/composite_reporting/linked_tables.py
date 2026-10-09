"""Lossless source projection for linked analysis; no investment arithmetic."""

from typing import Any

from app.composite_reporting.table_contract import resolve_source_pointer

POLICIES = {
    "cumulative_return": ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENT", 2),
    "return_value": ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENT", 2),
    "weight": ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENT", 2),
    "contribution": ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENTAGE_POINTS", 2),
    "linked_contribution": ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENTAGE_POINTS", 2),
    "total_linked_contribution": ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENTAGE_POINTS", 2),
    "reconciliation_difference": ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENTAGE_POINTS", 12),
    "display_rounding_difference": ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENTAGE_POINTS", 12),
    "linking_factor": ("DECIMAL_FACTOR", "DECIMAL_RATIO", "DECIMAL_RATIO", 12),
    "beginning_market_value": ("MONEY", "CURRENCY_UNITS", "CURRENCY_UNITS", 2),
    "participating_period_count": ("COUNT", "PERIOD_COUNT", "PERIOD_COUNT", 0),
}


def linked_report_facts() -> dict[str, Any]:
    return {
        "disclosures": [
            {
                "code": "CALCULATED_REPLAY_NOT_OFFICIAL",
                "text": (
                    "Explicit retained calculated replay; "
                    "official publication authority is not attested."
                ),
            },
            {
                "code": "SOURCE_QUALIFICATION",
                "text": "CALCULATED_ANALYSIS / RETAINED_SOURCE_ATTESTATION_NOT_LIVE_QUALIFIED",
            },
            {
                "code": "SOURCE_OWNED_LINKING",
                "text": (
                    "Performance owns returns, linking factors, contributions and differences. "
                    "Report performs no linking or residual allocation."
                ),
            },
            {
                "code": "PRECISION_AND_UNITS",
                "text": (
                    "Canonical decimal text is exact. Return percent and contribution percentage "
                    "points convert once for display. "
                    "Source totals and differences are not recomputed."
                ),
            },
        ],
        "uncaptured": [
            {
                "product": name,
                "availability": "UNAVAILABLE",
                "value": None,
                "reason_code": "SOURCE_PRODUCT_NOT_CAPTURED",
            }
            for name in (
                "CalendarReturns",
                "TrailingReturns",
                "SinceInception",
                "Risk",
                "Attribution",
                "ApprovedRestatement",
                "CompleteEligibilityPopulation",
            )
        ],
    }


def _table(
    data: dict[str, Any], table_id: str, fields: list[str], paths: list[tuple[str, str]]
) -> dict[str, Any]:
    columns = []
    for field in fields:
        kind, unit, display, places = POLICIES.get(field, ("TEXT", "TEXT", "TEXT", None))
        columns.append(
            {
                "column_id": field,
                "label": field.replace("_", " "),
                "value_type": kind,
                "unit": unit,
                "display_unit": display,
                "display_conversion": "RATIO_TO_PERCENT_DISPLAY"
                if display in {"PERCENT", "PERCENTAGE_POINTS"}
                else "IDENTITY",
                "display_decimal_places": places,
                "display_rounding_mode": "HALF_UP",
                "currency": data["source_response"]["reporting_currency"]
                if kind == "MONEY"
                else None,
                "scale": "1",
            }
        )
    rows = []
    for row_id, root in paths:
        cells = {}
        for field in fields:
            pointer = f"{root}/{field}"
            value = resolve_source_pointer(data, pointer)
            cells[field] = {
                "canonical_value": None if value is None else str(value),
                "availability": "UNAVAILABLE" if value is None else "AVAILABLE",
                "reason_codes": (
                    ["SOURCE_PRODUCT_NOT_CAPTURED"]
                    if root.startswith("/report_facts/uncaptured/")
                    else ["SOURCE_IDENTITY_NOT_PROVIDED"]
                )
                if value is None
                else [],
                "source_pointer": pointer,
            }
        rows.append({"row_id": row_id, "cells": cells})
    return {"table_id": table_id, "title": table_id, "columns": columns, "rows": rows}


def linked_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    source = data["source_response"]
    return [
        _table(
            data,
            "Summary",
            [
                "metric_id",
                "method",
                "status",
                "qualification",
                "constituent_decomposition",
                "period_start",
                "period_end",
                "return_view",
                "reporting_currency",
                "cumulative_return",
                "total_linked_contribution",
                "reconciliation_difference",
                "display_rounding_difference",
            ],
            [("linked-summary", "/source_response")],
        ),
        _table(
            data,
            "LinkedContribution",
            ["portfolio_id", "linked_contribution", "participating_period_count"],
            [(str(i), f"/source_response/members/{i}") for i in range(len(source["members"]))],
        ),
        _table(
            data,
            "LinkedPeriods",
            [
                "portfolio_id",
                "period_start",
                "period_end",
                "return_value",
                "beginning_market_value",
                "weight",
                "contribution",
                "linking_factor",
                "linked_contribution",
                "source_snapshot_id",
                "source_fingerprint",
                "calculation_id",
                "restatement_version",
                "restatement_sequence",
            ],
            [(str(i), f"/source_response/periods/{i}") for i in range(len(source["periods"]))],
        ),
        _table(data, "Methods", ["method", "units"], [("source-method", "/source_response")]),
        _table(
            data,
            "Lineage",
            ["engine_version", "calculation_fingerprint"],
            [("selection", "/source_response/selection_manifest")],
        ),
        _table(
            data,
            "Disclosures",
            ["code", "text"],
            [
                (str(i), f"/report_facts/disclosures/{i}")
                for i in range(len(data["report_facts"]["disclosures"]))
            ],
        ),
        _table(
            data,
            "UncapturedProducts",
            ["product", "availability", "value", "reason_code"],
            [
                (str(i), f"/report_facts/uncaptured/{i}")
                for i in range(len(data["report_facts"]["uncaptured"]))
            ],
        ),
    ]


def build_linked_dataset(
    *, selection: Any, admitted_tenant_id: str, status_code: int, payload: dict[str, Any]
) -> dict[str, Any]:
    from app.composite_reporting.linked_contract import (
        CompositeLinkedReportData,
        admit_linked_response,
    )

    admit_linked_response(
        selection=selection,
        admitted_tenant_id=admitted_tenant_id,
        status_code=status_code,
        payload=payload,
    )
    data = {
        "contract_version": "composite_review.v3",
        "qualification": "EXPLICIT_RETAINED_CALCULATED_REPLAY",
        "publication_state": "NOT_ATTESTED",
        "tenant_id": admitted_tenant_id,
        "selection": selection.model_dump(mode="json"),
        "source_response_digest": selection.response_digest,
        "source_response": payload,
        "report_facts": linked_report_facts(),
    }
    data["tables"] = linked_tables(data)
    CompositeLinkedReportData.model_validate(data)
    return data
