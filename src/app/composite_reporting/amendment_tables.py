"""Source-pointer projections of monthly corrections, without eligibility evaluation."""

import json
from collections.abc import Iterator
from typing import Any

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.eligibility_contract import proposal_for_month
from app.composite_reporting.eligibility_tables import (
    _cell,
    column_policy,
    eligibility_facts,
    eligibility_tables,
)


def amendment_facts(data: dict[str, Any]) -> dict[str, Any]:
    facts = eligibility_facts(data)
    facts["disclosures"].append(
        {
            "code": "SOURCE_CORRECTION_ONLY",
            "text": (
                "Ordinary-month source correction with unchanged approved policy "
                "and expected population. Full predecessor/original custody is retained; "
                "no Performance aggregates are joined. "
                "XLSX is unavailable until explicit Render and Archive v6 support."
            ),
        }
    )
    return facts


def _leaves(value: Any, pointer: str) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, pointer + "/" + str(index))
    else:
        yield pointer


def amendment_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    tables = eligibility_tables(data)
    rows: list[dict[str, Any]] = []
    for index, month in enumerate(data["source_months"]):
        proposal, suffix = proposal_for_month(month)
        paths = [(proposal["amendment"], f"/source_months/{index}/{suffix}/amendment")]
        for position, receipt in enumerate(month["lineage_receipts"]):
            base = f"/source_months/{index}/lineage_receipts/{position}"
            paths.append(
                (
                    {
                        key: receipt[key]
                        for key in (
                            "product_name",
                            "product_version",
                            "content_hash",
                            "publication_sequence",
                        )
                    },
                    base,
                )
            )
            if receipt["product_version"] == "v2":
                paths.append((receipt["lineage"], base + "/lineage"))
        for value, base in paths:
            for pointer in _leaves(value, base):
                rows.append(
                    {
                        "row_id": f"m{index}:a{len(rows)}",
                        "cells": {
                            "month": _cell(data, f"/selection/months/{index}/month"),
                            "value": _cell(data, pointer),
                        },
                    }
                )
    if len(rows) > 10000:
        raise CompositeEvidenceRefused("COMPOSITE_AMENDMENT_TABLE_CAPACITY_EXCEEDED")
    tables.append(
        {
            "table_id": "Amendments",
            "title": "Amendments",
            "columns": [
                column_policy(name, data["selection"]["reporting_currency"])
                for name in ("month", "value")
            ],
            "rows": rows,
        }
    )
    return tables


def build_amendment_dataset(selection: Any, months: list[dict[str, Any]]) -> dict[str, Any]:
    from app.composite_reporting.amendment_contract import CompositeAmendmentReportData

    data = {
        "contract_version": "composite_review.v6",
        "qualification": "CONTROLLED_MONTHLY_SOURCE_AMENDMENT_REPLAY",
        "publication_state": "NOT_ATTESTED",
        "tenant_id": selection.tenant_id,
        "selection": selection.model_dump(mode="json"),
        "source_months": months,
    }
    data["report_facts"] = amendment_facts(data)
    data["tables"] = amendment_tables(data)
    if len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()) > 8_388_608:
        raise CompositeEvidenceRefused("COMPOSITE_AMENDMENT_DATASET_CAPACITY_EXCEEDED")
    CompositeAmendmentReportData.model_validate(data)
    return data
