"""Deterministic complete provenance pointers, never shortened proof or raw bytes."""

from typing import Any

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.eligibility_contract import proposal_for_month
from app.composite_reporting.eligibility_tables import (
    _cell,
    column_policy,
    eligibility_facts,
    eligibility_tables,
)
from app.composite_reporting.table_contract import resolve_source_pointer

CALCULATION_BOUNDARY = (
    "Configured-identity controlled producer custody only; no cryptographic or bank "
    "provenance acceptance, TWR, MWR, dispersion, contribution or model-fee calculation. "
    "CONTROLLED / NOT_ATTESTED."
)


def historical_facts() -> dict[str, Any]:
    facts = eligibility_facts({})
    facts["no_amendment"] = None
    facts["evidence_roles"] = {
        key: key for key in ("POLICY_ADMISSION", "EVALUATION_PROPOSAL", "EVALUATION_APPROVAL")
    }
    facts["disclosures"].append(
        {"code": "HISTORICAL_POLICY_CUSTODY_BOUNDARY", "text": CALCULATION_BOUNDARY}
    )
    return facts


def scalar_pointers(value: Any, pointer: str) -> list[str]:
    """Object keys lexical, arrays original order; JSONB ordering is irrelevant."""
    if isinstance(value, dict):
        return [
            leaf
            for key in sorted(value)
            for leaf in scalar_pointers(
                value[key], pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            )
        ]
    if isinstance(value, list):
        return [
            leaf
            for index, child in enumerate(value)
            for leaf in scalar_pointers(child, f"{pointer}/{index}")
        ]
    return [pointer]


def provenance_paths(data: dict[str, Any]) -> list[tuple[int, str, str]]:
    paths: list[tuple[int, str, str]] = []
    for index, month in enumerate(data["source_months"]):
        _, suffix = proposal_for_month(month)
        proposals = [(f"/source_months/{index}/{suffix}", month.get("receipt"))]
        proposals.extend(
            (f"/source_months/{index}/lineage_receipts/{position}/approval/proposal", receipt)
            for position, receipt in enumerate(month["lineage_receipts"])
        )
        for base, receipt in proposals:
            for role, pointer in [
                ("POLICY_ADMISSION", base + "/policy_approval"),
                ("EVALUATION_PROPOSAL", base + "/operation_verification"),
            ]:
                paths.extend(
                    (index, role, leaf)
                    for leaf in scalar_pointers(resolve_source_pointer(data, pointer), pointer)
                )
            if receipt is not None:
                pointer = base.rsplit("/proposal", 1)[0] + "/operation_verification"
                paths.extend(
                    (index, "EVALUATION_APPROVAL", leaf)
                    for leaf in scalar_pointers(resolve_source_pointer(data, pointer), pointer)
                )
    return paths


def historical_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    tables = eligibility_tables(data)
    amendments: list[dict[str, Any]] = []
    for index, month in enumerate(data["source_months"]):
        proposal, suffix = proposal_for_month(month)
        pointers = []
        if proposal["product_version"] == "v4":
            root = f"/source_months/{index}/{suffix}/amendment"
            pointers.extend(scalar_pointers(proposal["amendment"], root))
            for position, receipt in enumerate(month["lineage_receipts"]):
                base = f"/source_months/{index}/lineage_receipts/{position}"
                pointers.extend(
                    base + "/" + key
                    for key in (
                        "product_name",
                        "product_version",
                        "content_hash",
                        "publication_sequence",
                    )
                )
                if receipt["product_version"] == "v4":
                    pointers.extend(scalar_pointers(receipt["lineage"], base + "/lineage"))
        for pointer in pointers or ["/report_facts/no_amendment"]:
            cell = _cell(data, pointer)
            if pointer == "/report_facts/no_amendment":
                cell.update(
                    availability="NOT_APPLICABLE", reason_codes=["ORDINARY_ROOT_NO_AMENDMENT"]
                )
            amendments.append(
                {
                    "row_id": f"m{index}:a{len(amendments)}",
                    "cells": {
                        "month": _cell(data, f"/selection/months/{index}/month"),
                        "value": cell,
                    },
                }
            )
    rows: list[dict[str, Any]] = []
    for index, role, pointer in provenance_paths(data):
        cell = _cell(data, pointer)
        if pointer.endswith("/scope/run_id") and cell["canonical_value"] is None:
            cell.update(availability="NOT_APPLICABLE", reason_codes=["POLICY_RUN_NOT_APPLICABLE"])
        rows.append(
            {
                "row_id": f"m{index}:p{len(rows)}",
                "cells": {
                    "month": _cell(data, f"/selection/months/{index}/month"),
                    "evidence_role": _cell(data, "/report_facts/evidence_roles/" + role),
                    "value": cell,
                },
            }
        )
    if max(len(amendments), len(rows)) > 10000:
        raise CompositeEvidenceRefused("COMPOSITE_HISTORICAL_TABLE_CAPACITY_EXCEEDED")
    for name, fields, values in [
        ("Amendments", ("month", "value"), amendments),
        ("PolicyAdmission", ("month", "evidence_role", "value"), rows),
    ]:
        tables.append(
            {
                "table_id": name,
                "title": name,
                "columns": [
                    column_policy(key, data["selection"]["reporting_currency"]) for key in fields
                ],
                "rows": values,
            }
        )
    return tables


def build_historical_dataset(selection: Any, months: list[dict[str, Any]]) -> dict[str, Any]:
    from app.composite_reporting.historical_contract import CompositeHistoricalReportData

    data = {
        "contract_version": "composite_review.v7",
        "qualification": "CONTROLLED_HISTORICAL_POLICY_EVIDENCE_REPLAY",
        "publication_state": "NOT_ATTESTED",
        "tenant_id": selection.tenant_id,
        "selection": selection.model_dump(mode="json"),
        "source_months": months,
        "report_facts": historical_facts(),
    }
    data["tables"] = historical_tables(data)
    CompositeHistoricalReportData.model_validate(data)
    return data
