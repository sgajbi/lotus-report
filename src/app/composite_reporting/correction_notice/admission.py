"""Semantic admission remains independent of caller recomputed wrapper hashes."""

from typing import Any

from app.composite_reporting.correction_notice.hashing import (
    digest,
    document_preimage,
    evidence_preimage,
    manifest_preimage,
    pair_preimage,
)
from app.composite_reporting.correction_notice.projection import (
    derive_facts,
    initial_obligations,
    pair_index,
    project_tables,
)
from app.composite_reporting.correction_notice.selection import SCOPE_AXES
from app.reporting_identity.capture_binding import source_revision_vector_for_capture
from app.reporting_identity.identity import factual_content_digest
from app.reporting_jobs.models import ReportStatusEvent
from app.reporting_lineage.store import compute_snapshot_hash
from app.reporting_render.archive_lineage import _acknowledged_relationship_id


def require_equal(actual: Any, expected: Any, code: str) -> None:
    if actual != expected:
        raise ValueError("COMPOSITE_NOTICE_" + code)


def source_custody_identity(generation: dict[str, Any]) -> dict[str, Any]:
    payload, capture = generation["payload"], generation["capture_identity"]
    identity = {
        **{
            key: payload[key]
            for key in (
                "contract_version",
                "qualification",
                "publication_state",
                "selection",
                "source_context",
            )
        },
        **{
            key: capture[key].removeprefix("sha256:")
            for key in ("series_digest", "source_revision_digest", "factual_content_digest")
        },
        "definition": {
            key: payload["definition"][key] for key in ("pin", "source_response_digest")
        },
    }
    if payload.get("source_products"):
        identity["source_products"] = [
            {key: product[key] for key in ("pin", "source_response_digest")}
            for product in payload["source_products"]
        ]
    return identity


def _require_generation(generation: dict[str, Any]) -> None:
    payload, capture = generation["payload"], generation["capture_identity"]
    require_equal(
        compute_snapshot_hash(payload),
        generation["selection"]["snapshot_hash"],
        "SNAPSHOT_HASH_CONFLICT",
    )
    require_equal(
        factual_content_digest(payload),
        capture["factual_content_digest"],
        "FACTUAL_DIGEST_CONFLICT",
    )
    vector = source_revision_vector_for_capture(
        snapshot_payload=payload, upstream_services=("lotus-manage", "lotus-performance")
    )
    require_equal(
        vector.canonical(), generation["source_revision_vector"], "SOURCE_VECTOR_CONFLICT"
    )
    require_equal(vector.digest(), capture["source_revision_digest"], "SOURCE_DIGEST_CONFLICT")


def _require_ack(edge: dict[str, Any], source_job: str) -> None:
    ack = edge["acknowledgement"]
    raw = ack["raw_payload"]
    require_equal(ack["digest"], digest(evidence_preimage(ack["kind"], raw)), "ACK_DIGEST_CONFLICT")
    if ack["kind"] == "REPORT_LINEAGE_EVENT":
        ReportStatusEvent.model_validate(raw)
        if set(raw) - set(ReportStatusEvent.model_fields):
            raise ValueError("COMPOSITE_NOTICE_ACK_EXTRA_FIELD")
        require_equal(raw["event_type"], "job_archive_lineage_recorded", "ACK_TYPE")
        require_equal(raw["event_schema_version"], "report-status-event.v1", "ACK_SCHEMA")
        require_equal(raw["event_family"], "job_lifecycle", "ACK_FAMILY")
        require_equal(raw["report_job_id"], source_job, "ACK_JOB")
        payload = raw["event_payload"]
    else:
        payload = raw
        allowed = {
            "lifecycle_relationship_id",
            "source_document_id",
            "target_document_id",
            "transition_type",
            "transition_reason",
            "transition_reason_code",
            "requested_by",
            "requested_at",
        }
        require_equal(set(raw), allowed, "ACK_RECORD_SHAPE")
    relationship = _acknowledged_relationship_id(
        payload,
        source_document_id=edge["source_document_id"],
        target_document_id=edge["target_document_id"],
        transition_type="correct",
    )
    require_equal(relationship, edge["relationship_id"], "ACK_RELATIONSHIP")


def require_path(data: dict[str, Any]) -> None:
    nodes, edges = data["archive_path"]["nodes"], data["archive_path"]["edges"]
    require_equal([n["role"] for n in nodes], ["O", "O_rr", "C", "C_rr"], "PATH_ROLES")
    require_equal([n["position"] for n in nodes], list(range(4)), "NODE_ORDER")
    require_equal([e["position"] for e in edges], list(range(3)), "EDGE_ORDER")
    if (
        len({n["document_id"] for n in nodes}) != 4
        or len({e["relationship_id"] for e in edges}) != 3
    ):
        raise ValueError("COMPOSITE_NOTICE_PATH_DUPLICATED")
    for index, node in enumerate(nodes):
        side = "before" if index < 2 else "after"
        generation = data["generations"][side]
        raw = node["document_response"]
        require_equal(node["economic_generation"], side, "NODE_GENERATION")
        require_equal(node["tenant_id"], data["tenant_id"], "NODE_TENANT")
        require_equal(
            raw["composite_report_identity"],
            source_custody_identity(generation),
            "NODE_SOURCE_IDENTITY",
        )
        require_equal(
            node["document_response_digest"], digest(document_preimage(raw)), "DOCUMENT_DIGEST"
        )
        for key in (
            "document_id",
            "tenant_id",
            "report_job_id",
            "snapshot_id",
            "report_revision_id",
        ):
            require_equal(node[key], raw[key], "DOCUMENT_FIELD")
        for key in ("report_job_id", "snapshot_id", "report_revision_id", "snapshot_hash"):
            require_equal(node[key], generation["selection"][key], "NODE_CAPTURE")
        require_equal(node["artifact_sha256"], "sha256:" + raw["checksum"], "ARTIFACT_HASH")
        require_equal(raw["declared_artifact_sha256"], raw["checksum"], "DECLARED_HASH")
        require_equal(raw["report_data_contract_version"], "composite_review.v8", "NODE_VERSION")
        require_equal(raw["template_version"], "v8", "NODE_TEMPLATE")
        if raw["purge_status"] == "purged":
            raise ValueError("COMPOSITE_NOTICE_SOURCE_UNAVAILABLE")
        if index < 3:
            require_equal(
                raw["superseded_by_document_id"], nodes[index + 1]["document_id"], "PATH_FORWARD"
            )
        if index > 0:
            require_equal(
                raw["correction_of_document_id"], nodes[index - 1]["document_id"], "PATH_ORIGIN"
            )
    for index, edge in enumerate(edges):
        require_equal(edge["source_document_id"], nodes[index]["document_id"], "EDGE_SOURCE")
        require_equal(edge["target_document_id"], nodes[index + 1]["document_id"], "EDGE_TARGET")
        require_equal(
            edge["semantic_role"],
            "ECONOMIC_CORRECTION" if index == 1 else "TECHNICAL_RERENDER",
            "EDGE_ROLE",
        )
        require_equal(
            edge["source_generation"], nodes[index]["economic_generation"], "EDGE_GENERATION"
        )
        require_equal(
            edge["target_generation"], nodes[index + 1]["economic_generation"], "EDGE_GENERATION"
        )
        require_equal(
            edge["acknowledgement"]["kind"],
            "ARCHIVE_RELATIONSHIP_RECORD" if index == 1 else "REPORT_LINEAGE_EVENT",
            "ACK_KIND",
        )
        _require_ack(edge, nodes[index]["report_job_id"])


def validate_notice(data: dict[str, Any]) -> None:
    before, after = data["generations"]["before"], data["generations"]["after"]
    for generation in (before, after):
        _require_generation(generation)
        require_equal(generation["source_identity"]["tenant_id"], data["tenant_id"], "TENANT")
    require_equal(before["source_identity"], after["source_identity"], "SCOPE")
    if (
        before["selection"] == after["selection"]
        or before["capture_identity"]["report_revision_id"]
        == after["capture_identity"]["report_revision_id"]
    ):
        raise ValueError("COMPOSITE_NOTICE_ECONOMIC_PAIR_REQUIRED")
    # Scope axes are metadata, never financial differences or a materiality test.
    require_equal(set(before["source_identity"]), set(SCOPE_AXES), "SCOPE_SHAPE")
    require_path(data)
    require_equal(data["obligations"], initial_obligations(), "OBLIGATION_PROMOTION")
    require_equal(data["pair_fingerprint"], digest(pair_preimage(data)), "PAIR_FINGERPRINT")
    require_equal(data["manifest_digest"], digest(manifest_preimage(data)), "MANIFEST_DIGEST")
    require_equal(data["pair_index"], pair_index(data["generations"]), "COMPLETE_COORDINATE_UNION")
    require_equal(data["notice_facts"], derive_facts(data), "DERIVED_FACTS")
    require_equal(data["tables"], project_tables(data), "COMPLETE_PROJECTION")


def build_notice(
    *, tenant_id: str, generations: dict[str, Any], archive_path: dict[str, Any]
) -> dict[str, Any]:
    from app.composite_reporting.correction_notice.contract import CorrectionNoticeData

    data: dict[str, Any] = {
        "contract_version": "composite_review.v9",
        "comparison_profile": "RETAINED_SAME_SCOPE_CORRECTION_NOTICE_V1",
        "tenant_id": tenant_id,
        "qualification": "EXPLICIT_RETAINED_CALCULATED_REPLAY",
        "publication_state": "NOT_ATTESTED",
        "notice_completeness": "PARTIAL",
        "generations": generations,
        "archive_path": archive_path,
        "obligations": initial_obligations(),
    }
    data["pair_fingerprint"] = digest(pair_preimage(data))
    data["manifest_digest"] = digest(manifest_preimage(data))
    data["pair_index"] = pair_index(generations)
    data["notice_facts"] = derive_facts(data)
    data["tables"] = project_tables(data)
    CorrectionNoticeData.model_validate(data)
    return data
