"""Compose the exact acknowledged chain from source-owned read evidence."""

from typing import Any

from app.composite_reporting.correction_notice.admission import require_equal
from app.composite_reporting.correction_notice.hashing import (
    digest,
    document_preimage,
    evidence_preimage,
)
from app.composite_reporting.correction_notice.selection import CorrectionNoticeSelection


def acknowledged_path(
    *,
    selection: CorrectionNoticeSelection,
    generations: dict[str, Any],
    documents: list[dict[str, Any]],
    relationship_record: dict[str, Any],
    report_events: dict[str, list[dict[str, Any]]],
    relationship_source_capture: str,
) -> dict[str, Any]:
    require_equal(len(documents), 4, "DOCUMENT_COUNT")
    require_equal(
        [document["document_id"] for document in documents],
        selection.source_document_ids,
        "ORDERED_DOCUMENTS",
    )
    nodes = []
    for index, (role, raw) in enumerate(zip(("O", "O_rr", "C", "C_rr"), documents, strict=True)):
        side = "before" if index < 2 else "after"
        nodes.append(
            {
                "position": index,
                "role": role,
                "document_id": raw["document_id"],
                "artifact_sha256": "sha256:" + raw["checksum"],
                "tenant_id": raw["tenant_id"],
                **{key: raw[key] for key in ("report_job_id", "snapshot_id", "report_revision_id")},
                # Archive does not store the Report snapshot hash. Bind it to the
                # selected native source capture, never claim it came from Archive.
                "snapshot_hash": generations[side]["selection"]["snapshot_hash"],
                "economic_generation": side,
                "document_response": raw,
                "document_response_digest": digest(document_preimage(raw)),
            }
        )
    edges = []
    for index, relationship_id in enumerate(selection.relationship_ids):
        if index == 1:
            kind, raw = "ARCHIVE_RELATIONSHIP_RECORD", relationship_record
            source_capture = relationship_source_capture
        else:
            kind = "REPORT_LINEAGE_EVENT"
            side = "before" if index == 0 else "after"
            candidates = [
                event
                for event in report_events[side]
                if event.get("event_type") == "job_archive_lineage_recorded"
                and event.get("event_payload", {}).get("lifecycle_relationship_id")
                == relationship_id
            ]
            require_equal(len(candidates), 1, "UNIQUE_LINEAGE_ACK_REQUIRED")
            raw = candidates[0]
            source_capture = "report-status-event:" + raw["status_event_id"]
        edges.append(
            {
                "position": index,
                "relationship_id": relationship_id,
                "source_document_id": nodes[index]["document_id"],
                "target_document_id": nodes[index + 1]["document_id"],
                "transition_type": "correct",
                "semantic_role": "ECONOMIC_CORRECTION" if index == 1 else "TECHNICAL_RERENDER",
                "source_generation": nodes[index]["economic_generation"],
                "target_generation": nodes[index + 1]["economic_generation"],
                "acknowledgement": {
                    "kind": kind,
                    "source_capture": source_capture,
                    "raw_payload": raw,
                    "digest": digest(evidence_preimage(kind, raw)),
                },
            }
        )
    return {"nodes": nodes, "edges": edges}
