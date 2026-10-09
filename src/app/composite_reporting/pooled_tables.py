"""Complete pooled evidence projection, retaining owner values without calculation."""

import json
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.models import PooledAnalysisSelection
from app.composite_reporting.pooled_contract import (
    CompositePooledReportData,
    admit_pooled_response,
    require_predecessor,
)
from app.composite_reporting.table_contract import resolve_source_pointer


def pooled_report_facts() -> dict[str, Any]:
    return {
        "disclosures": [
            {
                "code": "NON_OFFICIAL_CALCULATED_ANALYSIS",
                "text": (
                    "Retained source-owned pooled analysis; Report does not calculate XIRR or fees."
                ),
            },
            {
                "code": "NOT_ATTESTED",
                "text": (
                    "Source qualification and attestation are retained; "
                    "workbook custody confers no bank authority."
                ),
            },
            {
                "code": "EXACT_MONEY_FLOAT64_ROOT",
                "text": (
                    "Source money is exact decimal. "
                    "The root and convergence diagnostics are FLOAT64 evidence."
                ),
            },
            {
                "code": "EXPLICIT_OUTCOME",
                "text": (
                    "NOT_CALCULABLE has null returns. "
                    "Only explicitly elected FALLBACK_ANALYSIS permits a Dietz return."
                ),
            },
            {
                "code": "CORRECTION_SCOPE",
                "text": (
                    "A calculation correction is not a monthly eligibility amendment "
                    "or institutional approval."
                ),
            },
        ]
    }


def _column(field: str, kind: str, currency: str) -> dict[str, Any]:
    return {
        "column_id": field,
        "label": field.replace("_", " "),
        "value_type": kind,
        "unit": {"TEXT": "TEXT", "DECIMAL_RETURN": "DECIMAL_RATIO", "MONEY": "CURRENCY_UNITS"}[
            kind
        ],
        "display_unit": {"TEXT": "TEXT", "DECIMAL_RETURN": "PERCENT", "MONEY": "CURRENCY_UNITS"}[
            kind
        ],
        "display_conversion": "RATIO_TO_PERCENT_DISPLAY"
        if kind == "DECIMAL_RETURN"
        else "IDENTITY",
        "display_decimal_places": None
        if kind == "TEXT"
        else (6 if kind == "DECIMAL_RETURN" else 2),
        "display_rounding_mode": "HALF_UP",
        "currency": currency if kind == "MONEY" else None,
        "scale": "1",
    }


def _cell(data: dict[str, Any], pointer: str, *, json_value: bool = False) -> dict[str, Any]:
    value = resolve_source_pointer(data, pointer)
    return {
        "canonical_value": None
        if value is None
        else (
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            if json_value
            else str(value)
        ),
        "availability": "UNAVAILABLE" if value is None else "AVAILABLE",
        "reason_codes": ["SOURCE_VALUE_UNAVAILABLE"] if value is None else [],
        "source_pointer": pointer,
    }


def _table(
    data: dict[str, Any], name: str, columns: list[tuple[str, str]], roots: list[tuple[str, str]]
) -> dict[str, Any]:
    return {
        "table_id": name,
        "title": name,
        "columns": [
            _column(field, kind, data["selection"]["reporting_currency"]) for field, kind in columns
        ],
        "rows": [
            {
                "row_id": identity,
                "cells": {field: _cell(data, root + "/" + field) for field, kind in columns},
            }
            for identity, root in roots
        ],
    }


def _leaves(value: Any, pointer: str) -> Iterator[str]:
    if isinstance(value, dict) and value:
        for key in sorted(value):
            child = value[key]
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from _leaves(child, pointer + "/" + escaped)
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from _leaves(child, pointer + "/" + str(index))
    else:
        yield pointer


def _primary_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    source = data["source_response"]
    tables = [
        _table(
            data,
            "Summary",
            [
                (name, "TEXT")
                for name in (
                    "schema_version",
                    "metric_id",
                    "method",
                    "result_classification",
                    "calculation_id",
                    "correction_of_calculation_id",
                    "calculation_engine_version",
                    "input_manifest_digest",
                )
            ],
            [("result", "/source_response")],
        ),
        _table(
            data,
            "Outcome",
            [
                (name, "TEXT")
                for name in (
                    "availability",
                    "actual_method",
                    "units",
                    "root_precision",
                    "input_money_precision",
                )
            ]
            + [
                (name, "DECIMAL_RETURN")
                for name in ("return_value", "annualized_return", "holding_period_return")
            ],
            [("outcome", "/source_response/outcome")],
        ),
        _table(
            data,
            "MonetaryObservation",
            [
                (name, "TEXT")
                for name in (
                    "period_start",
                    "period_end",
                    "reporting_currency",
                    "source_manifest_id",
                )
            ]
            + [(name, "MONEY") for name in ("opening_value", "terminal_value")],
            [("observation", "/source_response/observation")],
        ),
        _table(
            data,
            "InvestorCashFlows",
            [("economic_date", "TEXT"), ("amount", "MONEY")],
            [
                (str(i), f"/source_response/observation/investor_cash_flows/{i}")
                for i in range(len(source["observation"]["investor_cash_flows"]))
            ],
        ),
        _table(
            data,
            "Policy",
            [
                (name, "TEXT")
                for name in (
                    "binding_id",
                    "owner",
                    "revision",
                    "content_hash",
                    "method",
                    "return_view",
                    "fee_basis",
                    "tax_basis",
                    "day_count_basis",
                    "date_basis",
                    "fallback_policy",
                )
            ],
            [("policy", "/source_response/observation/source_bundle/policy")],
        ),
        _table(
            data,
            "Disclosures",
            [("code", "TEXT"), ("text", "TEXT")],
            [
                (str(i), f"/report_facts/disclosures/{i}")
                for i in range(len(data["report_facts"]["disclosures"]))
            ],
        ),
    ]
    return tables


def _bundle_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    source = data["source_response"]
    bundle = source["observation"]["source_bundle"]
    bundle_root = "/source_response/observation/source_bundle"
    tables: list[dict[str, Any]] = []
    tables.extend(
        [
            _table(
                data,
                "Valuations",
                [
                    (name, "TEXT")
                    for name in (
                        "portfolio_id",
                        "economic_date",
                        "role",
                        "currency",
                        "timing",
                        "source_pin_id",
                        "source_row_id",
                        "units",
                    )
                ]
                + [("amount", "MONEY")],
                [
                    (str(i), f"{bundle_root}/valuations/{i}")
                    for i in range(len(bundle["valuations"]))
                ],
            ),
            _table(
                data,
                "PortfolioFlows",
                [
                    (name, "TEXT")
                    for name in (
                        "portfolio_id",
                        "economic_date",
                        "source_date",
                        "currency",
                        "timing",
                        "classification",
                        "source_pin_id",
                        "event_id",
                        "revision",
                        "lifecycle_status",
                        "units",
                    )
                ]
                + [("amount", "MONEY")],
                [(str(i), f"{bundle_root}/flows/{i}") for i in range(len(bundle["flows"]))],
            ),
            _table(
                data,
                "MemberControls",
                [
                    (name, "MONEY")
                    for name in (
                        "opening_value",
                        "terminal_value",
                        "external_flows",
                        "entry_capital",
                        "exit_capital",
                    )
                ],
                [
                    (
                        member,
                        "/source_response/observation/per_member_controls/"
                        + member.replace("~", "~0").replace("/", "~1"),
                    )
                    for member in sorted(source["observation"]["per_member_controls"])
                ],
            ),
            _table(
                data,
                "SourcePins",
                [
                    (name, "TEXT")
                    for name in (
                        "pin_id",
                        "owner",
                        "product_name",
                        "product_version",
                        "revision",
                        "source_cut_id",
                        "payload_digest",
                        "compatibility_group",
                        "coverage_from",
                        "coverage_to",
                        "completeness",
                    )
                ],
                [
                    (str(i), f"{bundle_root}/source_pins/{i}")
                    for i in range(len(bundle["source_pins"]))
                ],
            ),
            _table(
                data,
                "Membership",
                [
                    (name, "TEXT")
                    for name in (
                        "portfolio_id",
                        "effective_from",
                        "effective_to",
                        "status",
                        "source_row_id",
                        "reason_code",
                    )
                ],
                [
                    (str(i), f"{bundle_root}/membership/{i}")
                    for i in range(len(bundle["membership"]))
                ],
            ),
        ]
    )
    return tables


def pooled_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    tables = _primary_tables(data) + _bundle_tables(data)
    # Empty sourced collections have no fabricated placeholder row. Their exact
    # empty JSON array remains visible in the complete evidence projection.
    tables = [table for table in tables if table["rows"]]
    # Every source value, including additive diagnostics, original solver output,
    # source bodies, source vector and reasons has an exact canonical JSON cell.
    for table_id, field in (
        ("SourceEvidence", "source_response"),
        ("PredecessorEvidence", "predecessor_source_response"),
    ):
        if data[field] is None:
            continue
        pointers = list(_leaves(data[field], "/" + field))
        tables.append(
            {
                "table_id": table_id,
                "title": table_id,
                "columns": [_column("value", "TEXT", data["selection"]["reporting_currency"])],
                "rows": [
                    {"row_id": str(i), "cells": {"value": _cell(data, pointer, json_value=True)}}
                    for i, pointer in enumerate(pointers)
                ],
            }
        )
    return tables


def build_pooled_dataset(
    *,
    selection: PooledAnalysisSelection,
    admitted_tenant_id: str,
    status_code: int,
    payload: dict[str, Any],
    predecessor: dict[str, Any] | None,
) -> dict[str, Any]:
    admit_pooled_response(
        selection=selection,
        admitted_tenant_id=admitted_tenant_id,
        status_code=status_code,
        payload=payload,
    )
    require_predecessor(selection, predecessor)
    data: dict[str, Any] = {
        "contract_version": "composite_review.v5",
        "qualification": "EXPLICIT_RETAINED_CALCULATED_REPLAY",
        "publication_state": "NOT_ATTESTED",
        "tenant_id": admitted_tenant_id,
        "selection": selection.model_dump(mode="json"),
        "source_response_digest": response_digest(payload),
        "source_response": deepcopy(payload),
        "predecessor_source_response": deepcopy(predecessor),
        "report_facts": pooled_report_facts(),
    }
    data["tables"] = pooled_tables(data)
    if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 8 * 1024 * 1024:
        raise CompositeEvidenceRefused("COMPOSITE_POOLED_DATASET_CAPACITY_EXCEEDED")
    CompositePooledReportData.model_validate(data)
    return data
