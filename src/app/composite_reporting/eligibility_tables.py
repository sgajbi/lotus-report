"""Complete deterministic eligibility projection; no source decision arithmetic."""

import json
from decimal import ROUND_HALF_UP, Decimal, localcontext
from math import ceil
from typing import Any

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.eligibility_contract import proposal_for_month
from app.composite_reporting.table_contract import resolve_source_pointer

TABLE_FIELDS = {
    "Summary": (
        "month",
        "evidence_kind",
        "expected_count",
        "observed_count",
        "included_count",
        "excluded_count",
        "pending_review_count",
        "population_verification",
        "official_activation",
    ),
    "Members": ("month", "portfolio_id", "status", "observations_present"),
    "EligibilityAssessments": (
        "month",
        "portfolio_id",
        "rule",
        "outcome",
        "numerator",
        "denominator",
        "ratio",
        "gross_inflow",
        "gross_outflow",
        "admitted_flow_count",
    ),
    "EligibilityReasons": ("month", "portfolio_id", "rule", "kind", "reason_code"),
    "MembershipHistory": (
        "month",
        "membership_revision",
        "portfolio_id",
        "effective_from",
        "effective_to",
        "status",
        "reason_code",
        "discretionary",
        "approval_ref",
        "source_snapshot_id",
        "supersedes_membership_revision",
        "affected_from",
        "affected_to",
    ),
    "Methods": (
        "month",
        "profile_kind",
        "flow_threshold",
        "cash_threshold",
        "flow_measure",
        "flow_breach_operator",
        "cash_breach_operator",
        "reentry",
        "content_hash",
    ),
    "Lineage": ("month", "evidence_kind", "product", "revision", "content_hash", "response_digest"),
    "Disclosures": ("code", "text"),
}

COUNTS = {
    "expected_count",
    "observed_count",
    "included_count",
    "excluded_count",
    "pending_review_count",
}
MONEY = {"numerator", "denominator", "gross_inflow", "gross_outflow"}
RATIOS = {"ratio", "flow_threshold", "cash_threshold"}
BOOLEANS = {"observations_present", "discretionary"}


def column_policy(field: str, currency: str) -> dict[str, Any]:
    kind, unit, places = "TEXT", "TEXT", None
    if field in COUNTS:
        kind, unit, places = "COUNT", "PORTFOLIO_COUNT", 0
    elif field == "admitted_flow_count":
        kind, unit, places = "COUNT", "EVENT_COUNT", 0
    elif field in MONEY:
        kind, unit, places = "MONEY", "CURRENCY_UNITS", 2
    elif field in RATIOS:
        kind, unit, places = "DECIMAL_FACTOR", "DECIMAL_RATIO", 12
    elif field in BOOLEANS:
        kind, unit = "BOOLEAN", "BOOLEAN"
    return {
        "column_id": field,
        "label": field.replace("_", " "),
        "value_type": kind,
        "unit": unit,
        "display_unit": unit,
        "display_conversion": "IDENTITY",
        "display_decimal_places": places,
        "display_rounding_mode": "HALF_UP",
        "currency": currency if kind == "MONEY" else None,
        "scale": "1",
    }


def eligibility_facts(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "unavailable": None,
        "no_reasons": {
            "value": None,
            "statement": (
                "All captured assessment reason arrays for this month are empty; "
                "no applicable reason occurrences."
            ),
        },
        "reason_kinds": {"failure_reasons": "FAILURE", "unknown_reasons": "UNKNOWN"},
        "disclosures": [
            {
                "code": "CONTROLLED_SYNTHETIC_ONLY",
                "text": (
                    "Controlled synthetic source replay / NOT_ATTESTED. Population and "
                    "published completeness are UNVERIFIED; official activation is UNAVAILABLE."
                ),
            },
            {
                "code": "SOURCE_OWNED_DECISIONS",
                "text": (
                    "Manage owns eligibility and publication. Report preserves all three "
                    "assessments and does not evaluate thresholds or manufacture approvals."
                ),
            },
            {
                "code": "FIRST_REASON_LOSS_BOUNDARY",
                "text": (
                    "Membership reason_code is only the first reason. EligibilityReasons "
                    "preserves every failure and unknown occurrence; occurrences are not "
                    "unique excluded members."
                ),
            },
            {
                "code": "EVALUATED_IS_NOT_PUBLISHED",
                "text": (
                    "EVALUATED_ONLY retains a proposal and missing-observation UNKNOWN cases. "
                    "Approval, publication and membership history are unavailable for that "
                    "variant; no receipt is inferred."
                ),
            },
            {
                "code": "EXACT_HISTORY",
                "text": (
                    "History retains original inclusive revision intervals. Selected month "
                    "gaps are not filled; same-month policy diffs are not cross-month history. "
                    "Cleared cash alone does not imply re-entry."
                ),
            },
            {
                "code": "SOURCE_UNITS",
                "text": (
                    "Ratios are source decimal ratios, not return percentages. Money, "
                    "portfolio counts, event counts and booleans remain distinct. "
                    "Raw decimal spelling and nulls remain retained."
                ),
            },
        ],
    }


def _cell(data: dict[str, Any], pointer: str) -> dict[str, Any]:
    value = resolve_source_pointer(data, pointer)
    if not (value is None or isinstance(value, (str, int, bool))):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_CELL_SCALAR_REQUIRED")
    canonical = (
        None if value is None else str(value).lower() if isinstance(value, bool) else str(value)
    )
    availability = "AVAILABLE"
    reasons: list[str] = []
    if value is None:
        availability, reasons = _null_semantics(data, pointer)
    return {
        "canonical_value": canonical,
        "source_pointer": pointer,
        "availability": availability,
        "reason_codes": reasons,
    }


def _null_semantics(data: dict[str, Any], pointer: str) -> tuple[str, list[str]]:
    if pointer == "/report_facts/no_reasons/value":
        return "NOT_APPLICABLE", ["NO_APPLICABLE_REASONS"]
    if "/assessments/" in pointer:
        parent, field = pointer.rsplit("/", 1)
        assessment = resolve_source_pointer(data, parent)
        unused = (
            (MONEY | RATIOS | {"admitted_flow_count"})
            if assessment["rule"] == "READINESS"
            else (
                {"gross_inflow", "gross_outflow", "admitted_flow_count"}
                if assessment["rule"] == "CASH"
                else set()
            )
        )
        if field in unused:
            return "NOT_APPLICABLE", ["RULE_FIELD_NOT_APPLICABLE"]
    return "UNAVAILABLE", ["SOURCE_VALUE_UNAVAILABLE"]


def _row(data: dict[str, Any], row_id: str, pointers: dict[str, str]) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "cells": {field: _cell(data, pointer) for field, pointer in pointers.items()},
    }


def _blank(fields: tuple[str, ...], common: dict[str, str]) -> dict[str, str]:
    return {field: common.get(field, "/report_facts/unavailable") for field in fields}


def _evaluation_rows(data: dict[str, Any], rows: dict[str, list[Any]], index: int) -> None:
    month = data["source_months"][index]
    proposal, suffix = proposal_for_month(month)
    base = f"/source_months/{index}/{suffix}"
    root = f"{base}/evaluation"
    common = {
        "month": f"/selection/months/{index}/month",
        "evidence_kind": f"/source_months/{index}/evidence_kind",
    }
    rows["Summary"].append(
        _row(
            data,
            f"m{index}",
            {field: common.get(field, f"{root}/{field}") for field in TABLE_FIELDS["Summary"]},
        )
    )
    policy = f"{root}/resolved_policy"
    rows["Methods"].append(
        _row(
            data,
            f"m{index}",
            {field: common.get(field, f"{policy}/{field}") for field in TABLE_FIELDS["Methods"]},
        )
    )
    reason_start = len(rows["EligibilityReasons"])
    for member_index, member in enumerate(proposal["evaluation"]["portfolios"]):
        member_root = f"{root}/portfolios/{member_index}"
        member_common = {**common, "portfolio_id": f"{member_root}/portfolio_id"}
        row_id = f"m{index}:p{member_index}"
        rows["Members"].append(
            _row(
                data,
                row_id,
                {
                    field: member_common.get(field, f"{member_root}/{field}")
                    for field in TABLE_FIELDS["Members"]
                },
            )
        )
        for rule_index, assessment in enumerate(member["assessments"]):
            rule_root = f"{member_root}/assessments/{rule_index}"
            rows["EligibilityAssessments"].append(
                _row(
                    data,
                    f"{row_id}:a{rule_index}",
                    {
                        field: member_common.get(field, f"{rule_root}/{field}")
                        for field in TABLE_FIELDS["EligibilityAssessments"]
                    },
                )
            )
            for kind in ("failure_reasons", "unknown_reasons"):
                for ordinal in range(len(assessment[kind])):
                    rows["EligibilityReasons"].append(
                        _row(
                            data,
                            f"{row_id}:a{rule_index}:{kind}:{ordinal}",
                            {
                                "month": common["month"],
                                "portfolio_id": member_common["portfolio_id"],
                                "rule": f"{rule_root}/rule",
                                "kind": f"/report_facts/reason_kinds/{kind}",
                                "reason_code": f"{rule_root}/{kind}/{ordinal}",
                            },
                        )
                    )
    if len(rows["EligibilityReasons"]) == reason_start:
        rows["EligibilityReasons"].append(
            _row(
                data,
                f"m{index}:not_applicable",
                {
                    field: common.get(field, "/report_facts/no_reasons/value")
                    for field in TABLE_FIELDS["EligibilityReasons"]
                },
            )
        )


def _history_rows(data: dict[str, Any], rows: dict[str, list[Any]], index: int) -> None:
    month = data["source_months"][index]
    common = {"month": f"/selection/months/{index}/month"}
    if month["evidence_kind"] == "EVALUATED_ONLY":
        rows["MembershipHistory"].append(
            _row(data, f"m{index}:unavailable", _blank(TABLE_FIELDS["MembershipHistory"], common))
        )
        return
    for product in ("parent_membership", "membership"):
        base = f"/source_months/{index}/{product}"
        metadata = {
            field: f"{base}/{field}"
            for field in (
                "membership_revision",
                "supersedes_membership_revision",
                "affected_from",
                "affected_to",
            )
        }
        for ordinal, _ in enumerate(month[product]["decisions"]):
            rows["MembershipHistory"].append(
                _row(
                    data,
                    f"m{index}:{product}:d{ordinal}",
                    {
                        field: {**common, **metadata}.get(
                            field, f"{base}/decisions/{ordinal}/{field}"
                        )
                        for field in TABLE_FIELDS["MembershipHistory"]
                    },
                )
            )


def _lineage_rows(data: dict[str, Any], rows: dict[str, list[Any]], index: int) -> None:
    month = data["source_months"][index]
    _, suffix = proposal_for_month(month)
    common = {
        "month": f"/selection/months/{index}/month",
        "evidence_kind": f"/source_months/{index}/evidence_kind",
    }
    products = [
        (
            suffix,
            "evaluation_revision",
            "receipt" if month["evidence_kind"] == "PUBLISHED" else "proposal",
        )
    ]
    if month["evidence_kind"] == "PUBLISHED":
        products += [
            ("membership", "membership_revision", "membership"),
            ("parent_membership", "membership_revision", "parent_membership"),
            ("universe", "attestation_version", "universe"),
            ("receipt", "publication_sequence", "receipt"),
        ]
    for ordinal, (path, revision, digest_key) in enumerate(products):
        base = f"/source_months/{index}/{path}"
        rows["Lineage"].append(
            _row(
                data,
                f"m{index}:s{ordinal}",
                {
                    **common,
                    "product": f"{base}/product_name",
                    "revision": f"{base}/{revision}",
                    "content_hash": f"{base}/content_hash",
                    "response_digest": f"/source_months/{index}/response_digests/{digest_key}",
                },
            )
        )


def eligibility_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: dict[str, list[Any]] = {name: [] for name in TABLE_FIELDS}
    for index in range(len(data["source_months"])):
        _evaluation_rows(data, rows, index)
        _history_rows(data, rows, index)
        _lineage_rows(data, rows, index)
    for index in range(len(data["report_facts"]["disclosures"])):
        rows["Disclosures"].append(
            _row(
                data,
                f"d{index}",
                {
                    field: f"/report_facts/disclosures/{index}/{field}"
                    for field in TABLE_FIELDS["Disclosures"]
                },
            )
        )
    if any(len(values) > 10000 for values in rows.values()):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_TABLE_CAPACITY_EXCEEDED")
    return [
        {
            "table_id": name,
            "title": name,
            "columns": [
                column_policy(field, data["selection"]["reporting_currency"]) for field in fields
            ],
            "rows": rows[name],
        }
        for name, fields in TABLE_FIELDS.items()
    ]


def build_eligibility_dataset(selection: Any, months: list[dict[str, Any]]) -> dict[str, Any]:
    from app.composite_reporting.eligibility_admission import require_source_months
    from app.composite_reporting.eligibility_contract import (
        CompositeEligibilityReportData,
    )

    require_source_months(selection, months)
    data = {
        "contract_version": "composite_review.v4",
        "qualification": "CONTROLLED_ELIGIBILITY_SOURCE_REPLAY",
        "publication_state": "NOT_ATTESTED",
        "tenant_id": selection.tenant_id,
        "selection": selection.model_dump(mode="json"),
        "source_months": months,
    }
    data["report_facts"] = eligibility_facts(data)
    data["tables"] = eligibility_tables(data)
    if len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 8_388_608:
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_DATASET_CAPACITY_EXCEEDED")
    CompositeEligibilityReportData.model_validate(data)
    return data


CAPACITY_POLICY = {
    "max_request_body_bytes": 8_388_608,
    "max_rows_per_sheet": 1000,
    "max_total_rows": 30_000,
    "max_total_cells": 210_000,
    "max_total_text_bytes": 16_777_216,
    "max_sheets": 64,
    "max_columns": 100,
    "max_cell_utf16_units": 32_767,
    "max_output_bytes": 16_777_216,
    "json_chunk_characters": 16_000,
}


def _display(column: dict[str, Any], cell: dict[str, Any]) -> str:
    value = cell["canonical_value"]
    if value is None:
        return f"{cell['availability']}: {', '.join(cell['reason_codes'])}"
    places = column["display_decimal_places"]
    if places is None:
        return str(value)
    with localcontext() as context:
        context.prec = max(80, len(value) + 20)
        number = Decimal(value)
        if column["display_conversion"] == "RATIO_TO_PERCENT_DISPLAY":
            number *= 100
        display = format(number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")
    suffix = {"PERCENT": "%", "PERCENTAGE_POINTS": " pp"}.get(column["display_unit"], "")
    if column["value_type"] == "MONEY":
        suffix = f" {column['currency']}"
    return display + suffix


def _identity_fragments(fields: dict[str, Any]) -> list[tuple[str, str]]:
    result = []
    for key, value in fields.items():
        canonical = json.dumps(value, ensure_ascii=True, sort_keys=True)
        if len(canonical) <= CAPACITY_POLICY["max_cell_utf16_units"]:
            result.append((key, canonical))
        else:
            from hashlib import sha256

            count = ceil(len(canonical) / 16000)
            descriptor = {
                "encoding": "ordered_json_text_v1",
                "count": count,
                "utf8_bytes": len(canonical),
                "sha256": sha256(canonical.encode("ascii")).hexdigest(),
            }
            result.append((key + "__chunks", json.dumps(descriptor, sort_keys=True)))
            result.extend(
                (
                    f"{key}__chunk_{index:06d}",
                    json.dumps(canonical[index * 16000 : (index + 1) * 16000]),
                )
                for index in range(count)
            )
    return result


def eligibility_workbook_projection(
    package: dict[str, Any],
) -> list[tuple[list[str], list[list[str]]]]:
    """Dry literal projection using the pinned Render overhead contract, without a writer."""
    data = package["report_data"]
    projected = []
    evidence, policies = [], []
    for table in data["tables"]:
        projected.append(
            (
                ["Report row identity", *(column["label"] for column in table["columns"])],
                [
                    [
                        row["row_id"],
                        *(
                            _display(column, row["cells"][column["column_id"]])
                            for column in table["columns"]
                        ),
                    ]
                    for row in table["rows"]
                ],
            )
        )
        for column in table["columns"]:
            policies.append(
                [
                    table["table_id"],
                    column["column_id"],
                    column["label"],
                    column["value_type"],
                    column["unit"],
                    column["display_unit"],
                    column["display_conversion"],
                    str(column["display_decimal_places"]),
                    column["display_rounding_mode"],
                    column["currency"] or "",
                    column["scale"],
                    "LITERAL_TEXT",
                ]
            )
        for row in table["rows"]:
            for column in table["columns"]:
                cell = row["cells"][column["column_id"]]
                evidence.append(
                    [
                        table["table_id"],
                        row["row_id"],
                        column["column_id"],
                        json.dumps(cell["canonical_value"], ensure_ascii=True),
                        cell["availability"],
                        json.dumps(cell["reason_codes"], ensure_ascii=True),
                        cell["source_pointer"],
                    ]
                )
    projected.append(
        (
            [
                "Table",
                "Row",
                "Column",
                "Canonical JSON scalar",
                "Availability",
                "Reasons JSON",
                "Source pointer",
            ],
            evidence,
        )
    )
    projected.append(
        (
            [
                "Table",
                "Column",
                "Label",
                "Type",
                "Source unit",
                "Display unit",
                "Conversion",
                "Decimal places",
                "Rounding",
                "Currency",
                "Scale",
                "Storage",
            ],
            policies,
        )
    )
    fields = {
        key: package[key]
        for key in (
            "render_package_version",
            "render_job_id",
            "report_job_id",
            "snapshot_id",
            "template_id",
            "template_version",
            "report_data_contract_version",
            "output_format",
            "lineage_refs",
            "disclosure_refs",
            "render_context",
        )
    }
    fields.update(
        {
            "template_digest": "sha256:" + "0" * 64,
            "canonical_precision": "Exact source text; never Excel numeric storage",
            "display_rounding": (
                "Declared column decimal places; HALF_UP; source ratios remain ratios"
            ),
            "metadata_policy": "Fixed creation date 2000-01-01; no wall-clock content",
        }
    )
    projected.append(
        (["Field", "Exact JSON value"], [list(row) for row in _identity_fragments(fields)])
    )
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    projected.append(
        (
            ["Chunk", "Canonical pinned dataset JSON"],
            [
                [str(index // 16000), canonical[index : index + 16000]]
                for index in range(0, len(canonical), 16000)
            ],
        )
    )
    return projected


def preflight_eligibility_package(
    package: dict[str, Any], *, failure_prefix: str = "COMPOSITE_ELIGIBILITY"
) -> dict[str, int]:
    """Refuse measured request and workbook limits before Render/Archive side effects.

    Output ZIP size remains Render's writer guard; it cannot be truthfully predicted
    without writing the artifact. No limit is increased and no content is truncated.
    """
    body_size = len(
        json.dumps(package, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )
    totals = {
        "request_body_bytes": body_size,
        "total_rows": 0,
        "total_cells": 0,
        "total_text_bytes": 0,
        "sheets": 0,
    }
    for headers, rows in eligibility_workbook_projection(package):
        partitions = max(1, ceil(len(rows) / CAPACITY_POLICY["max_rows_per_sheet"]))
        totals["sheets"] += partitions
        totals["total_rows"] += len(rows)
        totals["total_cells"] += (len(rows) + partitions) * len(headers)
        if len(headers) > CAPACITY_POLICY["max_columns"]:
            raise CompositeEvidenceRefused(f"{failure_prefix}_WORKBOOK_CAPACITY_EXCEEDED")
        for row in [headers] * partitions + rows:
            for value in row:
                if len(value.encode("utf-16-le")) // 2 > CAPACITY_POLICY[
                    "max_cell_utf16_units"
                ] or any(ord(char) < 32 and char not in "\t\r\n" for char in value):
                    raise CompositeEvidenceRefused(f"{failure_prefix}_WORKBOOK_LITERAL_INVALID")
                totals["total_text_bytes"] += len(value.encode("utf-8"))
    if any(value > CAPACITY_POLICY["max_" + key] for key, value in totals.items()):
        raise CompositeEvidenceRefused(f"{failure_prefix}_WORKBOOK_CAPACITY_EXCEEDED")
    return totals
