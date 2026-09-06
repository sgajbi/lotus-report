from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.reporting_jobs.models import (
    ReportJobLedgerRecord,
    ReportPortfolioMemoryArtifactRef,
    ReportPortfolioMemoryEvent,
    ReportPortfolioMemoryEventsResponse,
    ReportPortfolioMemoryGovernancePolicy,
    ReportPortfolioMemorySourceRef,
    ReportPortfolioMemorySupportabilityState,
    ReportStatusEvent,
)
from app.reporting_lineage.models import ReportInputSnapshotRecord

EVENT_IDENTITY_SCHEME = (
    "source_system:source_type:source_id:content_hash_or_content_hash_unavailable"
)
#: Version of the identity PREIMAGE, distinct from the identity's textual
#: shape above. v2 hashes only facts that were true when the event
#: happened and can never change afterwards; v1 additionally hashed the
#: job's CURRENT snapshot, artifact and archive-document facts, so a
#: finished historical event changed identity as the job progressed
#: through capture and archiving - within one deployment, with no code
#: change (report#283).
EVENT_IDENTITY_PREIMAGE_VERSION = "eip2"
#: What a consumer holding v1 identities can and cannot be promised. v1
#: hashes were computed at read time and never persisted anywhere, so
#: they are not reconstructible from retained evidence and this service
#: does not claim otherwise: a consumer re-keys once on its first read
#: under v2, matching events by the STABLE event_id
#: (report-memory:<job>:<status event>), which never changed under either
#: version. From v2 onward an event's identity never moves again.
EVENT_IDENTITY_COMPATIBILITY = (
    "v1 identities were computed at read time and never stored; they are not reconstructible. "
    "Re-key once by event_id, which is stable across both versions. v2 identities are fixed at "
    "event time and never change afterwards."
)
#: Statuses a job can only be in once its input snapshot exists; an
#: event before that point cites no snapshot, because none existed.
_SNAPSHOT_BEARING_STATUSES = {
    "data_ready",
    "rendering",
    "completed",
    "completed_with_warnings",
    "archiving",
    "archived",
    "failed",
    "cancelled",
}
RETENTION_POLICY = "DPM_PORTFOLIO_MEMORY_SOURCE_LINEAGE_7Y"
REDACTION_POLICY = "NO_RAW_PAYLOADS"
AUDIT_POLICY = "AUDIT_READ_AND_EXPORT"
ACCESS_CLASSIFICATION = "CLIENT_CONFIDENTIAL_INTERNAL"

REPORT_EVENT_TYPE_BY_LEDGER_EVENT = {
    "job_accepted": "REPORT_JOB_ACCEPTED",
    "job_collecting_data": "REPORT_DATA_COLLECTION_STARTED",
    "job_data_ready": "REPORT_SNAPSHOT_CAPTURED",
    "job_rendering": "REPORT_RENDER_STARTED",
    "job_completed": "REPORT_RENDER_COMPLETED",
    "job_archiving": "REPORT_ARCHIVE_HANDOFF_STARTED",
    "job_archived": "REPORT_ARCHIVED",
    "job_failed": "REPORT_JOB_FAILED",
    "job_cancelled": "REPORT_JOB_CANCELLED",
    "job_rerender_requested": "REPORT_RERENDER_REQUESTED",
    "job_rerender_archived": "REPORT_RERENDER_ARCHIVED",
    "job_rerender_failed": "REPORT_RERENDER_FAILED",
    "job_regenerate_requested": "REPORT_REGENERATE_REQUESTED",
    "job_regenerate_created": "REPORT_REGENERATE_CREATED",
    "job_regenerate_failed": "REPORT_REGENERATE_FAILED",
    "job_replay_requested": "REPORT_REPLAY_REQUESTED",
    "job_replay_created": "REPORT_REPLAY_CREATED",
    "job_replay_failed": "REPORT_REPLAY_FAILED",
}

READY_STATUSES = {"data_ready", "completed", "archived"}
DEGRADED_STATUSES = {"failed", "cancelled", "completed_with_warnings"}


def build_report_portfolio_memory_events(
    *,
    record: ReportJobLedgerRecord,
    status_events: list[ReportStatusEvent],
    snapshot: ReportInputSnapshotRecord | None,
    generated_at: datetime | None = None,
) -> ReportPortfolioMemoryEventsResponse:
    """Build support-safe report-owned source events for downstream portfolio memory."""
    response_generated_at = generated_at or datetime.now(UTC)
    events = [
        _build_event(record=record, status_event=status_event, snapshot=snapshot)
        for status_event in status_events
    ]
    supportability_state = _aggregate_supportability(events)
    response_hash = _hash_payload(
        {
            "report_job_id": record.job_id,
            "event_hashes": [event.content_hash for event in events],
            "supportability_state": supportability_state,
        }
    )
    return ReportPortfolioMemoryEventsResponse(
        report_job_id=record.job_id,
        portfolio_id=_primary_portfolio_id(record),
        report_type=record.report_type,
        event_count=len(events),
        supportability_state=supportability_state,
        source_systems=["lotus-report"],
        reason_codes=[_response_reason_code(supportability_state)],
        governance_policy=ReportPortfolioMemoryGovernancePolicy(
            event_identity_scheme=EVENT_IDENTITY_SCHEME,
            retention_policy=RETENTION_POLICY,
            redaction_policy=REDACTION_POLICY,
            audit_policy=AUDIT_POLICY,
            access_classification=ACCESS_CLASSIFICATION,
        ),
        content_hash=response_hash,
        generated_at=response_generated_at,
        events=events,
    )


def _build_event(
    *,
    record: ReportJobLedgerRecord,
    status_event: ReportStatusEvent,
    snapshot: ReportInputSnapshotRecord | None,
) -> ReportPortfolioMemoryEvent:
    supportability_state = _event_supportability(status_event)
    event_type = REPORT_EVENT_TYPE_BY_LEDGER_EVENT.get(
        status_event.event_type,
        "REPORT_LIFECYCLE_EVENT",
    )
    # EVERY member is a fact of the event itself, fixed when the status
    # event was written: the job it belongs to, the transition it records,
    # and when. Nothing here can be changed by a later lifecycle step -
    # which is exactly the property v1 lacked. Facts that arrive later
    # (snapshot, artifact, archive document, report revision) reach the
    # consumer through refs and fields OUTSIDE this preimage, each
    # attached only to events at or after the step that produced it.
    safe_payload = {
        "identity_preimage_version": EVENT_IDENTITY_PREIMAGE_VERSION,
        "report_job_id": record.job_id,
        "report_type": record.report_type,
        "status_event_id": status_event.status_event_id,
        # The DURABLE ledger event type, never the presentation mapping
        # above: a later release that adds a mapping for an event type
        # currently falling back to REPORT_LIFECYCLE_EVENT would otherwise
        # restate the identity of every historical instance - the same
        # defect class in a slower form.
        "event_type": status_event.event_type,
        "status": status_event.to_status,
        "portfolio_id": _primary_portfolio_id(record),
        "created_at": status_event.created_at.isoformat(),
    }
    content_hash = _hash_payload(safe_payload)
    return ReportPortfolioMemoryEvent(
        event_id=f"report-memory:{record.job_id}:{status_event.status_event_id}",
        event_identity=(
            f"lotus-report:REPORT_STATUS_EVENT:{status_event.status_event_id}:{content_hash}"
        ),
        event_type=event_type,
        event_time=status_event.created_at,
        actor=status_event.actor,
        source_system="lotus-report",
        source_type="REPORT_STATUS_EVENT",
        source_id=status_event.status_event_id,
        portfolio_id=_primary_portfolio_id(record),
        report_job_id=record.job_id,
        report_type=record.report_type,
        status=status_event.to_status,
        supportability_state=supportability_state,
        summary=_event_summary(record=record, status_event=status_event, event_type=event_type),
        reason_codes=[event_type],
        source_refs=_source_refs(record=record, status_event=status_event, snapshot=snapshot),
        artifact_refs=_artifact_refs(record=record, status_event=status_event),
        content_hash=content_hash,
        metadata={
            "correlation_id": status_event.correlation_id,
            "trace_id": status_event.trace_id,
            "report_request_id": record.request_id,
            "idempotency_key": record.idempotency_key,
        },
    )


def _source_refs(
    *,
    record: ReportJobLedgerRecord,
    status_event: ReportStatusEvent,
    snapshot: ReportInputSnapshotRecord | None,
) -> list[ReportPortfolioMemorySourceRef]:
    refs = [
        ReportPortfolioMemorySourceRef(
            source_system="lotus-report",
            source_type="REPORT_JOB",
            source_id=record.job_id,
            content_hash=f"sha256:{record.request_hash}",
        ),
        ReportPortfolioMemorySourceRef(
            source_system="lotus-report",
            source_type="REPORT_STATUS_EVENT",
            source_id=status_event.status_event_id,
            content_hash=_hash_payload(
                {
                    "status_event_id": status_event.status_event_id,
                    "event_type": status_event.event_type,
                    "to_status": status_event.to_status,
                    "created_at": status_event.created_at.isoformat(),
                }
            ),
        ),
    ]
    # The snapshot did not exist when the job was accepted, so an
    # accepted event must not cite it: an event states what was true when
    # it happened. Consumers that deduplicate on identity keep the FIRST
    # body they saw, so a body that grew later would never reach them
    # anyway - stating it only from the producing step onward keeps body
    # and identity telling the same story.
    if snapshot is not None and _snapshot_existed_at(status_event, snapshot):
        refs.append(
            ReportPortfolioMemorySourceRef(
                source_system="lotus-report",
                source_type="REPORT_INPUT_SNAPSHOT",
                source_id=snapshot.snapshot_id,
                content_hash=snapshot.snapshot_hash,
            )
        )
        if snapshot.report_revision_id:
            # The canonical revision of the facts this report presents
            # (report#283). Enrichment, deliberately OUTSIDE the identity
            # preimage: a revision minted at capture must never restate
            # the identity of an event that was already finished.
            refs.append(
                ReportPortfolioMemorySourceRef(
                    source_system="lotus-report",
                    source_type="REPORT_REVISION",
                    source_id=snapshot.report_revision_id,
                    content_hash=snapshot.factual_content_digest,
                )
            )
    return refs


def _snapshot_existed_at(
    status_event: ReportStatusEvent,
    snapshot: ReportInputSnapshotRecord,
) -> bool:
    """Whether this event happened at or after the snapshot's creation.

    Two independent readings, because either alone has a blind spot. The
    timestamp comparison is the direct question and catches events that
    record the snapshot's own creation while the job is still
    ``collecting_data`` - the retained-snapshot replay clone appends
    ``job_replay_snapshot_cloned`` before the transition to
    ``data_ready``, and that event of all events must cite the snapshot
    it created. The status floor stands behind it: any event at or after
    ``data_ready`` certainly follows capture, whatever the recorded
    timestamps do.
    """

    return (
        snapshot.created_at <= status_event.created_at
        or status_event.to_status in _SNAPSHOT_BEARING_STATUSES
    )


def _artifact_refs(
    *,
    record: ReportJobLedgerRecord,
    status_event: ReportStatusEvent,
) -> list[ReportPortfolioMemoryArtifactRef]:
    refs: list[ReportPortfolioMemoryArtifactRef] = []
    if status_event.to_status in {"completed", "archiving", "archived"} and record.render_job_id:
        refs.append(
            ReportPortfolioMemoryArtifactRef(
                artifact_system="lotus-render",
                artifact_type="RENDERED_REPORT_ARTIFACT",
                artifact_id=record.render_job_id,
                content_hash=record.render_artifact_sha256,
            )
        )
    if status_event.to_status == "archived" and record.archive_document_id:
        refs.append(
            ReportPortfolioMemoryArtifactRef(
                artifact_system="lotus-archive",
                artifact_type="ARCHIVED_REPORT_DOCUMENT",
                artifact_id=record.archive_document_id,
                content_hash=record.render_artifact_sha256,
            )
        )
    return refs


def _event_supportability(
    status_event: ReportStatusEvent,
) -> ReportPortfolioMemorySupportabilityState:
    if status_event.to_status in READY_STATUSES:
        return "READY"
    if status_event.to_status in DEGRADED_STATUSES:
        return "DEGRADED"
    return "PENDING_REVIEW"


def _aggregate_supportability(
    events: list[ReportPortfolioMemoryEvent],
) -> ReportPortfolioMemorySupportabilityState:
    if not events:
        return "EMPTY"
    states = {event.supportability_state for event in events}
    if "DEGRADED" in states:
        return "DEGRADED"
    if "PENDING_REVIEW" in states and "READY" not in states:
        return "PENDING_REVIEW"
    return "READY"


def _response_reason_code(state: ReportPortfolioMemorySupportabilityState) -> str:
    if state == "READY":
        return "REPORT_EVENT_FAMILY_READY"
    if state == "DEGRADED":
        return "REPORT_EVENT_FAMILY_DEGRADED"
    if state == "EMPTY":
        return "REPORT_EVENT_FAMILY_EMPTY"
    return "REPORT_EVENT_FAMILY_PENDING_REVIEW"


def _event_summary(
    *,
    record: ReportJobLedgerRecord,
    status_event: ReportStatusEvent,
    event_type: str,
) -> str:
    if status_event.message:
        return status_event.message
    return f"{event_type.replace('_', ' ').title()} for {record.report_type}."


def _primary_portfolio_id(record: ReportJobLedgerRecord) -> str | None:
    portfolio_ids = record.portfolio_scope.get("portfolio_ids")
    if isinstance(portfolio_ids, list) and portfolio_ids:
        return str(portfolio_ids[0])
    return None


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
