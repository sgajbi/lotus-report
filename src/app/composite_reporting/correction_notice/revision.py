"""Source-stated retained Report capture and Archive artifact pins for notice identity."""

from typing import Any

from app.reporting_identity.identity import SourceRevision


def notice_source_revisions(payload: dict[str, Any]) -> list[SourceRevision]:
    if payload.get("contract_version") != "composite_review.v9":
        return []
    revisions = [
        SourceRevision(
            source_service="lotus-report",
            source_product="composite_review",
            source_product_version="v8",
            source_snapshot_id=generation["selection"]["snapshot_id"],
            content_hash=generation["selection"]["snapshot_hash"],
            restatement_version=generation["selection"]["report_revision_id"],
            supportability_status="EXPLICIT_RETAINED_CALCULATED_REPLAY",
        )
        for generation in payload["generations"].values()
    ]
    revisions.extend(
        SourceRevision(
            source_service="lotus-archive",
            source_product="generated_document",
            source_snapshot_id=node["document_id"],
            content_hash=node["artifact_sha256"],
            restatement_version=node["report_revision_id"],
            supportability_status="NOT_ATTESTED",
        )
        for node in payload["archive_path"]["nodes"]
    )
    return revisions
