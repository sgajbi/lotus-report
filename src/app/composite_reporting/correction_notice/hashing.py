"""Versioned exact canonical preimages; arrays preserve admitted order."""

import hashlib
import json
from typing import Any

from app.composite_reporting.correction_notice.selection import SELECTOR

OPERATION = "ORDER_RETAINED_COMPOSITE_NOTICE"
RECIPE = "report446-v9-32-table-projection.r4"
NODE_FIELDS = (
    "position",
    "role",
    "document_id",
    "artifact_sha256",
    "tenant_id",
    "report_job_id",
    "snapshot_id",
    "report_revision_id",
    "snapshot_hash",
    "economic_generation",
    "document_response_digest",
)
EDGE_FIELDS = (
    "position",
    "relationship_id",
    "source_document_id",
    "target_document_id",
    "transition_type",
    "semantic_role",
    "source_generation",
    "target_generation",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def evidence_preimage(kind: str, raw_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": "lotus-report/composite-notice/acknowledgement.v1",
        "kind": kind,
        "raw_payload": raw_payload,
    }


def document_preimage(raw_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": "lotus-report/composite-notice/archive-document.v1",
        "raw_payload": raw_payload,
    }


def pair_preimage(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": "lotus-report/composite-notice/pair.v1",
        "operation": OPERATION,
        "selector": SELECTOR,
        "tenant_id": data["tenant_id"],
        "before": data["generations"]["before"]["selection"],
        "after": data["generations"]["after"]["selection"],
        "ordered_nodes": [
            {key: node[key] for key in NODE_FIELDS} for node in data["archive_path"]["nodes"]
        ],
        "ordered_edges": [
            {
                **{key: edge[key] for key in EDGE_FIELDS},
                "acknowledgement_digest": edge["acknowledgement"]["digest"],
            }
            for edge in data["archive_path"]["edges"]
        ],
    }


def manifest_preimage(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": "lotus-report/composite-notice/manifest.v1",
        "operation": OPERATION,
        "selector": SELECTOR,
        **{
            key: data[key]
            for key in (
                "tenant_id",
                "qualification",
                "publication_state",
                "notice_completeness",
                "pair_fingerprint",
                "generations",
                "archive_path",
                "obligations",
            )
        },
        "projection_recipe_id": RECIPE,
    }
