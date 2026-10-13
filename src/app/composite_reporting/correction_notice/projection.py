"""Complete copied side tables and exact coordinate union; no effect arithmetic."""

from copy import deepcopy
from typing import Any

from app.composite_reporting.correction_notice.hashing import canonical_bytes
from app.composite_reporting.correction_notice.selection import SOURCE_TABLES
from app.composite_reporting.table_contract import resolve_source_pointer

OBLIGATION_REASONS = {
    "before_after_results": (
        "CAPTURED_INTERNAL",
        "RETAINED_SOURCE_FACTS_ONLY",
        ["/generations/before", "/generations/after"],
    ),
    "affected_periods": (
        "CAPTURED_INTERNAL",
        "RETAINED_REPORT_HORIZON_ONLY",
        [
            f"/generations/{side}/payload/selection/{key}"
            for side in ("before", "after")
            for key in ("period_start", "period_end")
        ],
    ),
    "reason": ("UNAVAILABLE", "AUTHORITATIVE_REPORT_REASON_NOT_CAPTURED", []),
    "materiality": ("UNAVAILABLE", "AUTHORITATIVE_MATERIALITY_NOT_CAPTURED", []),
    "report_approval": ("UNAVAILABLE", "REPORT_PUBLICATION_APPROVAL_NOT_CAPTURED", []),
    "report_replacements": (
        "CAPTURED_INTERNAL",
        "ACKNOWLEDGED_SOURCE_PATH_ONLY",
        ["/archive_path/edges/0", "/archive_path/edges/1", "/archive_path/edges/2"],
    ),
    "explanation_delta_interaction": (
        "UNAVAILABLE",
        "CMP_LIN_006_SUPPLIER_PRODUCT_NOT_CAPTURED",
        [],
    ),
    "recipient_distribution": ("UNAVAILABLE", "CMP_RPT_009_DELIVERY_REGISTER_NOT_CAPTURED", []),
}
SCOPE_FIELDS = (
    ("profile", "/comparison_profile"),
    ("contract_version", "/contract_version"),
    ("tenant_id", "/tenant_id"),
    *(
        (key, f"/generations/before/payload/selection/{key}")
        for key in (
            "composite_id",
            "period_start",
            "period_end",
            "reporting_currency",
            "return_view",
            "methodology",
            "engine_version",
        )
    ),
    *(
        (f"{side}_{key}", f"/generations/{side}/selection/{key}")
        for side in ("before", "after")
        for key in ("report_job_id", "snapshot_id", "report_revision_id", "snapshot_hash")
    ),
    ("pair_fingerprint", "/pair_fingerprint"),
    ("manifest_digest", "/manifest_digest"),
    ("qualification", "/qualification"),
    ("publication_state", "/publication_state"),
    ("completeness", "/notice_completeness"),
)
FACT_COLUMNS = {
    "NoticeScope": ("field", "value", "source_pointer"),
    "PairIndex": (
        "source_table_id",
        "source_row_id",
        "source_column_id",
        "presence",
        "before_cell_ref",
        "after_cell_ref",
    ),
    "ArchivePath": (
        "edge_index",
        "source_document_id",
        "target_document_id",
        "relationship_id",
        "wire_transition_type",
        "semantic_role",
        "source_generation",
        "target_generation",
        "acknowledgement_evidence_digest",
    ),
    "Obligations": ("obligation_id", "status", "reason_code", "evidence_refs"),
}
FACT_TITLES = (
    "Notice scope",
    "Complete source coordinate union",
    "Acknowledged source path",
    "Partial obligation evidence",
)
FACT_KEYS = ("scope", "pair_index", "archive_path", "obligations")


def initial_obligations() -> dict[str, Any]:
    return {
        key: {"status": state, "reason_code": reason, "evidence_refs": list(refs)}
        for key, (state, reason, refs) in OBLIGATION_REASONS.items()
    }


def _coordinates(
    generation: dict[str, Any], side: str
) -> dict[tuple[str, str, str], dict[str, Any]]:
    result = {}
    for ti, table in enumerate(generation["payload"]["tables"]):
        for ri, row in enumerate(table["rows"]):
            for ci, column in enumerate(table["columns"]):
                key = table["table_id"], row["row_id"], column["column_id"]
                result[key] = {
                    "generation": side,
                    "table_index": ti,
                    "row_index": ri,
                    "column_index": ci,
                    "table_id": key[0],
                    "row_id": key[1],
                    "column_id": key[2],
                    "source_pointer": row["cells"][key[2]]["source_pointer"],
                }
    return result


def pair_index(generations: dict[str, Any]) -> list[dict[str, Any]]:
    before = _coordinates(generations["before"], "before")
    after = _coordinates(generations["after"], "after")
    table_rank = {key: index for index, key in enumerate(SOURCE_TABLES)}
    column_rank = {}
    for generation in generations.values():
        for table in generation["payload"]["tables"]:
            for index, column in enumerate(table["columns"]):
                column_rank[table["table_id"], column["column_id"]] = index
    result = []
    for key in sorted(
        before.keys() | after.keys(),
        key=lambda k: (table_rank[k[0]], k[1], column_rank[k[0], k[2]]),
    ):
        row_id = canonical_bytes(list(key)).decode("ascii")
        if len(row_id) > 256:
            raise ValueError("COMPOSITE_NOTICE_COORDINATE_TOO_LONG")
        result.append(
            {
                "row_id": row_id,
                "coordinate": dict(zip(("table_id", "row_id", "column_id"), key, strict=True)),
                "presence": "BOTH_PRESENT"
                if key in before and key in after
                else "BEFORE_ONLY"
                if key in before
                else "AFTER_ONLY",
                "before_ref": before.get(key),
                "after_ref": after.get(key),
            }
        )
    return result


def derive_facts(data: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "scope": [
            {
                "field": field,
                "value": str(resolve_source_pointer(data, path)),
                "source_pointer": path,
            }
            for field, path in SCOPE_FIELDS
        ],
        "pair_index": [],
        "archive_path": [],
        "obligations": [
            {
                "obligation_id": key,
                **{k: value[k] for k in ("status", "reason_code")},
                "evidence_refs": canonical_bytes(value["evidence_refs"]).decode("ascii"),
            }
            for key, value in data["obligations"].items()
        ],
    }
    for item in data["pair_index"]:
        facts["pair_index"].append(
            {
                **{f"source_{key}": value for key, value in item["coordinate"].items()},
                "presence": item["presence"],
                **{
                    f"{side}_cell_ref": None
                    if item[f"{side}_ref"] is None
                    else canonical_bytes(item[f"{side}_ref"]).decode("ascii")
                    for side in ("before", "after")
                },
            }
        )
    for edge in data["archive_path"]["edges"]:
        facts["archive_path"].append(
            {
                "edge_index": str(edge["position"]),
                **{
                    key: edge[key]
                    for key in (
                        "source_document_id",
                        "target_document_id",
                        "relationship_id",
                        "semantic_role",
                        "source_generation",
                        "target_generation",
                    )
                },
                "wire_transition_type": edge["transition_type"],
                "acknowledgement_evidence_digest": edge["acknowledgement"]["digest"],
            }
        )
    return facts


def _text_column(key: str) -> dict[str, Any]:
    return {
        "column_id": key,
        "label": key,
        "value_type": "TEXT",
        "unit": "TEXT",
        "display_unit": "TEXT",
        "display_conversion": "IDENTITY",
        "display_decimal_places": None,
        "display_rounding_mode": "HALF_UP",
        "currency": None,
        "scale": "1",
    }


def project_tables(data: dict[str, Any]) -> list[dict[str, Any]]:
    tables = []
    for side in ("before", "after"):
        for original in data["generations"][side]["payload"]["tables"]:
            table = deepcopy(original)
            table["table_id"] = side.capitalize() + "_" + original["table_id"]
            for row in table["rows"]:
                for cell in row["cells"].values():
                    cell["source_pointer"] = f"/generations/{side}/payload" + cell["source_pointer"]
            tables.append(table)
    for (table_id, columns), title, fact_key in zip(
        FACT_COLUMNS.items(), FACT_TITLES, FACT_KEYS, strict=True
    ):
        rows = []
        for index, fact in enumerate(data["notice_facts"][fact_key]):
            row_id = (
                fact["field"]
                if fact_key == "scope"
                else data["pair_index"][index]["row_id"]
                if fact_key == "pair_index"
                else f"edge:{index}"
                if fact_key == "archive_path"
                else fact["obligation_id"]
            )
            rows.append(
                {
                    "row_id": row_id,
                    "cells": {
                        key: {
                            "canonical_value": fact[key],
                            "availability": "AVAILABLE"
                            if fact[key] is not None
                            else "NOT_APPLICABLE",
                            "reason_codes": []
                            if fact[key] is not None
                            else ["SOURCE_COORDINATE_NOT_PRESENT"],
                            "source_pointer": f"/notice_facts/{fact_key}/{index}/{key}",
                        }
                        for key in columns
                    },
                }
            )
        tables.append(
            {
                "table_id": table_id,
                "title": title,
                "columns": [_text_column(key) for key in columns],
                "rows": rows,
            }
        )
    return tables
