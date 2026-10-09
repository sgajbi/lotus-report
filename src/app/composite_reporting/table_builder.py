"""Echo source facts into semantic tables; no analytics or investment formulas."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.composite_reporting.table_contract import (
    CompositeCell,
    CompositeColumn,
    CompositeRow,
    CompositeTable,
    resolve_source_pointer,
    validate_cell_lineage,
)

_TEXT = ("TEXT", "TEXT", "TEXT", "IDENTITY", None)
_RETURN = ("DECIMAL_RETURN", "DECIMAL_RATIO", "PERCENT", "RATIO_TO_PERCENT_DISPLAY", 2)
_CONTRIBUTION = (
    "DECIMAL_RETURN",
    "DECIMAL_RATIO",
    "PERCENTAGE_POINTS",
    "RATIO_TO_PERCENT_DISPLAY",
    2,
)
_MONEY = ("MONEY", "CURRENCY_UNITS", "CURRENCY_UNITS", "IDENTITY", 2)
_COUNT = ("COUNT", "PORTFOLIO_COUNT", "PORTFOLIO_COUNT", "IDENTITY", 0)


def build_composite_tables(dataset: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(dataset)
    result["report_facts"] = _report_facts(dataset)
    tables = [
        _summary(result),
        _periods(result),
        _contributions(result),
        _methods(result),
        _lineage(result),
        _disclosures(result),
        *_uncaptured_tables(result),
    ]
    validate_cell_lineage(result, tables)
    result["tables"] = [table.model_dump(mode="json") for table in tables]
    return result


def _report_facts(dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        "qualification": dataset["qualification"],
        "publication_state": dataset["publication_state"],
        "source_response_digest": dataset["source_response_digest"],
        "authority": {
            "receipt": None,
            "control_revision": None,
            "availability": "UNAVAILABLE",
            "reason_code": "SOURCE_AUTHORITY_NOT_ATTESTED",
        },
        "unavailable_contribution": None,
        "disclosures": [
            {
                "code": "CALCULATED_REPLAY_NOT_OFFICIAL",
                "text": (
                    "Explicit retained calculated replay. "
                    "Official publication authority is not attested."
                ),
            },
            {
                "code": "FINANCIAL_VALUES_SOURCE_OWNED",
                "text": (
                    "Performance owns every return, asset, weight and contribution. "
                    "Report does not recalculate."
                ),
            },
            {
                "code": "EXACT_VALUES_AND_DISPLAY",
                "text": (
                    "Canonical decimal text is exact. Numeric Excel display uses declared "
                    "units and rounding; retain the canonical companion for precision."
                ),
            },
            {
                "code": "NULL_IS_NOT_ZERO",
                "text": (
                    "Zero is measured. Unavailable, unknown and not-applicable cells "
                    "are null with reasons."
                ),
            },
            {
                "code": "COUNT_SCOPE",
                "text": (
                    "Member count is ready financial facts. Excluded member count is "
                    "non-ready facts, not a unique policy-exclusion population or reason total."
                ),
            },
        ],
        "uncaptured": {
            key: {"metric": label, "value": None, "reason_code": "SOURCE_PRODUCT_NOT_CAPTURED"}
            for key, label in (
                ("AnnualReturns", "Calendar-year returns"),
                ("Risk", "Risk analytics"),
                ("Members", "Complete expected member population"),
                ("EligibilityReasons", "Policy eligibility reasons"),
                ("MembershipHistory", "Historical membership decisions"),
                ("Attribution", "Benchmark-relative attribution"),
                ("Restatement", "Approved correction comparison"),
            )
        },
    }


def _column(key: str, label: str, policy: tuple[Any, ...], currency: str) -> CompositeColumn:
    kind, unit, display, conversion, places = policy
    return CompositeColumn.model_validate(
        {
            "column_id": key,
            "label": label,
            "value_type": kind,
            "unit": unit,
            "display_unit": display,
            "display_conversion": conversion,
            "display_decimal_places": places,
            "currency": currency if kind == "MONEY" else None,
            "scale": "1",
        }
    )


def _table(
    dataset: dict[str, Any],
    table_id: str,
    columns: list[tuple[str, str, tuple[Any, ...]]],
    rows: list[tuple[str, dict[str, str]]],
) -> CompositeTable:
    return CompositeTable(
        table_id=table_id,
        title=table_id,
        columns=[
            _column(key, label, policy, dataset["selection"]["reporting_currency"])
            for key, label, policy in columns
        ],
        rows=[
            CompositeRow(
                row_id=row_id,
                cells={key: _cell(dataset, pointer) for key, pointer in pointers.items()},
            )
            for row_id, pointers in rows
        ],
    )


def _cell(dataset: dict[str, Any], pointer: str) -> CompositeCell:
    value = resolve_source_pointer(dataset, pointer)
    return CompositeCell(
        canonical_value=None if value is None else str(value),
        availability="AVAILABLE" if value is not None else "UNAVAILABLE",
        reason_codes=[]
        if value is not None
        else [
            "SOURCE_AUTHORITY_NOT_ATTESTED"
            if "/authority/" in pointer
            else "SOURCE_PRODUCT_NOT_CAPTURED"
            if "/uncaptured/" in pointer
            else "SOURCE_VALUE_UNAVAILABLE"
        ],
        source_pointer=pointer,
    )


def _summary(dataset: dict[str, Any]) -> CompositeTable:
    fields = [
        ("composite_id", "Composite", _TEXT, "/selection/composite_id"),
        ("period_start", "Inclusive start", _TEXT, "/selection/period_start"),
        ("period_end", "Inclusive end", _TEXT, "/selection/period_end"),
        ("return_view", "Fee view", _TEXT, "/selection/return_view"),
        ("currency", "Currency", _TEXT, "/selection/reporting_currency"),
        (
            "return",
            "Requested horizon cumulative return",
            _RETURN,
            "/source_response/cumulative_return",
        ),
        ("status", "Source calculation status", _TEXT, "/source_response/status"),
        ("qualification", "Evidence qualification", _TEXT, "/report_facts/qualification"),
        ("publication", "Publication authority", _TEXT, "/report_facts/publication_state"),
        ("authority_receipt", "Authority receipt", _TEXT, "/report_facts/authority/receipt"),
        ("control_revision", "Control revision", _TEXT, "/report_facts/authority/control_revision"),
    ]
    return _table(
        dataset,
        "Summary",
        [(key, label, policy) for key, label, policy, _ in fields],
        [("summary", {key: pointer for key, _, _, pointer in fields})],
    )


def _periods(dataset: dict[str, Any]) -> CompositeTable:
    columns = [
        ("period_start", "Inclusive start", _TEXT),
        ("period_end", "Inclusive end", _TEXT),
        ("status", "Source status", _TEXT),
        ("return_value", "Period return", _RETURN),
        ("cumulative_return", "Cumulative return through period", _RETURN),
        ("beginning_market_value", "Beginning assets", _MONEY),
        ("ending_market_value", "Ending assets", _MONEY),
        ("member_count", "Ready fact count", _COUNT),
        ("excluded_member_count", "Non-ready fact count", _COUNT),
        ("dispersion_equal_weight", "Period member dispersion", _RETURN),
        ("return_view", "Fee view", _TEXT),
        ("reporting_currency", "Currency", _TEXT),
        ("restatement_sequence", "Fact sequence", _TEXT),
    ]
    rows = [
        (str(index), {key: f"/source_response/periods/{index}/{key}" for key, _, _ in columns})
        for index in range(len(dataset["source_response"]["periods"]))
    ]
    # Producer windows may be arbitrary intervals. Never label a daily or
    # partial-month source window as a complete monthly return.
    monthly = all(
        window["period_start"].endswith("-01")
        and _month_end(window["period_end"])
        and window["period_start"][:7] == window["period_end"][:7]
        for window in dataset["selection"]["windows"]
    )
    return _table(dataset, "MonthlyReturns" if monthly else "PeriodReturns", columns, rows)


def _month_end(value: str) -> bool:
    from datetime import date, timedelta

    return (date.fromisoformat(value) + timedelta(days=1)).day == 1


def _contributions(dataset: dict[str, Any]) -> CompositeTable:
    columns = [
        ("portfolio_id", "Member identifier", _TEXT),
        ("period_start", "Inclusive start", _TEXT),
        ("period_end", "Inclusive end", _TEXT),
        ("return_value", "Member return", _RETURN),
        ("beginning_market_value", "Beginning assets", _MONEY),
        ("beginning_asset_weight", "Beginning asset weight", _RETURN),
        ("contribution", "Return contribution (percentage points)", _CONTRIBUTION),
        ("source_snapshot_id", "Member snapshot", _TEXT),
        ("source_fingerprint", "Member fingerprint", _TEXT),
        ("restatement_version", "Fact version", _TEXT),
        ("restatement_sequence", "Fact sequence", _TEXT),
    ]
    rows = [
        (
            f"{period_index}:{member_index}",
            {
                key: (
                    f"/source_response/periods/{period_index}/member_contributions/"
                    f"{member_index}/{key}"
                )
                for key, _, _ in columns
            },
        )
        for period_index, period in enumerate(dataset["source_response"]["periods"])
        for member_index in range(len(period["member_contributions"]))
    ]
    if not rows:
        return _table(
            dataset,
            "Contribution",
            [("value", "Member contribution evidence", _TEXT)],
            [("unavailable", {"value": "/report_facts/unavailable_contribution"})],
        )
    return _table(dataset, "Contribution", columns, rows)


def _methods(dataset: dict[str, Any]) -> CompositeTable:
    pointers = ["/selection/methodology", "/selection/engine_version"]
    for index, window in enumerate(dataset["selection"]["windows"]):
        for key in window["method_binding"]:
            escaped = key.replace("~", "~0").replace("/", "~1")
            pointers.append(f"/selection/windows/{index}/method_binding/{escaped}")
    return _pointer_table(dataset, "Methods", pointers)


def _lineage(dataset: dict[str, Any]) -> CompositeTable:
    pointers = [
        "/selection/calculation_id",
        "/selection/calculation_fingerprint",
        "/report_facts/source_response_digest",
    ]
    for index in range(len(dataset["selection"]["windows"])):
        pointers.extend(
            f"/selection/windows/{index}/{key}"
            for key in (
                "materialization_id",
                "definition_content_hash",
                "membership_content_hash",
                "attestation_content_hash",
                "source_cut_id",
                "retained_receipt_fingerprint",
            )
        )
    return _pointer_table(dataset, "Lineage", pointers)


def _pointer_table(dataset: dict[str, Any], name: str, pointers: list[str]) -> CompositeTable:
    # Labels themselves are Report-owned facts; retain them as explicit semantic
    # facts so even a label has a resolvable lineage pointer, never self-reference.
    dataset["report_facts"][name] = pointers
    return _table(
        dataset,
        name,
        [("field", "Evidence path", _TEXT), ("value", "Exact value", _TEXT)],
        [
            (str(index), {"field": f"/report_facts/{name}/{index}", "value": pointer})
            for index, pointer in enumerate(pointers)
        ],
    )


def _disclosures(dataset: dict[str, Any]) -> CompositeTable:
    return _table(
        dataset,
        "Disclosures",
        [("code", "Disclosure", _TEXT), ("text", "Text", _TEXT)],
        [
            (
                str(index),
                {key: f"/report_facts/disclosures/{index}/{key}" for key in ("code", "text")},
            )
            for index in range(len(dataset["report_facts"]["disclosures"]))
        ],
    )


def _uncaptured_tables(dataset: dict[str, Any]) -> list[CompositeTable]:
    return [
        _table(
            dataset,
            name,
            [
                ("metric", "Uncaptured evidence", _TEXT),
                ("value", "Value", _TEXT),
                ("reason_code", "Reason", _TEXT),
            ],
            [
                (
                    "unavailable",
                    {
                        key: f"/report_facts/uncaptured/{name}/{key}"
                        for key in ("metric", "value", "reason_code")
                    },
                )
            ],
        )
        for name in dataset["report_facts"]["uncaptured"]
    ]
