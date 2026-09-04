"""Correction/replacement lineage into Archive's lifecycle API (report#266).

Report owns the correction and replacement INTENT; Archive owns the durable
document LIFECYCLE. Once a rerender correction or regenerate replacement
reaches verified custody, the old-document -> new-document linkage is
recorded through Archive's own lifecycle transitions - never through
create-document metadata, which silently ignored supersession fields for the
relay's entire life.

Failure discipline: a lineage-recording failure is explicit and recoverable.
The attempt outcome is written durably as a job event either way -
``job_archive_lineage_recorded`` or ``job_archive_lineage_pending`` - and a
pending pair is re-attempted by ``settle_pending_archive_lineage`` on the
next correction-flow entry. Archive replays an already-recorded (old, new,
type) pair idempotently, so retries converge. The successfully stored new
document is never destroyed or unarchived because its linkage is pending.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from app.reporting_jobs.models import ReportCallerContext

LINEAGE_RECORDED_EVENT = "job_archive_lineage_recorded"
LINEAGE_PENDING_EVENT = "job_archive_lineage_pending"
LINEAGE_REFUSED_EVENT = "job_archive_lineage_refused"


class ArchiveLineageClient(Protocol):
    async def record_lifecycle_transition(
        self,
        *,
        source_document_id: str,
        target_document_id: str,
        transition_type: str,
        transition_reason: str,
        actor_id: str,
        tenant_id: str,
        region: str,
        correlation_id: str,
        trace_id: str,
        booking_center_code: str | None = None,
        role: str | None = None,
    ) -> tuple[int, dict[str, Any]]: ...


class LineageEventLedger(Protocol):
    def append_job_event(
        self,
        *,
        job_id: str,
        event_type: str,
        message: str,
        event_payload: dict[str, Any] | None = None,
        event_idempotency_key: str | None = None,
        actor: str,
        correlation_id: str,
        trace_id: str,
        skip_if_idempotency_key_exists: bool = False,
    ) -> bool: ...

    def list_status_events(self, job_id: str) -> Sequence[Any]: ...


def _pair_key(source_document_id: str, target_document_id: str, transition_type: str) -> str:
    return f"{transition_type}:{source_document_id}->{target_document_id}"


async def record_archive_lineage(
    *,
    archive_client: ArchiveLineageClient,
    ledger: LineageEventLedger,
    event_job_id: str,
    source_document_id: str,
    target_document_id: str,
    transition_type: str,
    transition_reason: str,
    caller_context: ReportCallerContext,
) -> bool:
    """Record one lineage pair; True when Archive holds it.

    Never raises: the outcome - recorded or pending - is written durably to
    the job's event stream, and a pending pair is picked up by settlement.
    """

    pair = _pair_key(source_document_id, target_document_id, transition_type)
    try:
        status_code, _payload = await archive_client.record_lifecycle_transition(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            transition_type=transition_type,
            transition_reason=transition_reason,
            actor_id=caller_context.triggered_by,
            tenant_id=caller_context.tenant_id,
            region=caller_context.region,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            booking_center_code=caller_context.booking_center_code,
            role=caller_context.role,
        )
    except Exception:
        status_code = 0
    if status_code in {200, 201}:
        ledger.append_job_event(
            job_id=event_job_id,
            event_type=LINEAGE_RECORDED_EVENT,
            message=(
                f"Archive lifecycle records {source_document_id} "
                f"{transition_type}d by {target_document_id}."
            ),
            event_payload={
                "source_document_id": source_document_id,
                "target_document_id": target_document_id,
                "transition_type": transition_type,
            },
            event_idempotency_key=f"{LINEAGE_RECORDED_EVENT}:{pair}",
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            skip_if_idempotency_key_exists=True,
        )
        return True
    if 400 <= status_code < 500:
        # A contract/policy refusal is TERMINAL for this pair: retrying the
        # identical transition cannot converge, so it is surfaced for
        # operator attention instead of hiding inside an endless pending
        # retry. The stored document still stands.
        ledger.append_job_event(
            job_id=event_job_id,
            event_type=LINEAGE_REFUSED_EVENT,
            message=(
                f"Archive REFUSED lifecycle linkage {source_document_id} -> "
                f"{target_document_id} ({transition_type}) with status "
                f"{status_code}. This pair will not be retried; operator "
                "attention is required."
            ),
            event_payload={
                "source_document_id": source_document_id,
                "target_document_id": target_document_id,
                "transition_type": transition_type,
                "status_code": status_code,
            },
            event_idempotency_key=f"{LINEAGE_REFUSED_EVENT}:{pair}",
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            skip_if_idempotency_key_exists=True,
        )
        return False
    ledger.append_job_event(
        job_id=event_job_id,
        event_type=LINEAGE_PENDING_EVENT,
        message=(
            f"Archive lifecycle linkage {source_document_id} -> "
            f"{target_document_id} ({transition_type}) is PENDING: the "
            "lifecycle call did not succeed. The stored document stands; "
            "the linkage is re-attempted on the next correction-flow entry."
        ),
        event_payload={
            "source_document_id": source_document_id,
            "target_document_id": target_document_id,
            "transition_type": transition_type,
            "transition_reason": transition_reason,
        },
        event_idempotency_key=f"{LINEAGE_PENDING_EVENT}:{pair}",
        actor=caller_context.triggered_by,
        correlation_id=caller_context.correlation_id,
        trace_id=caller_context.trace_id,
        skip_if_idempotency_key_exists=True,
    )
    return False


async def settle_pending_archive_lineage(
    *,
    archive_client: ArchiveLineageClient,
    ledger: LineageEventLedger,
    event_job_id: str,
    caller_context: ReportCallerContext,
) -> None:
    """Re-attempt every pending lineage pair that has no recorded outcome.

    Idempotent end to end: Archive replays known pairs, and the recorded
    event is keyed by the pair.
    """

    recorded: set[str] = set()
    pending: dict[str, dict[str, Any]] = {}
    for event in ledger.list_status_events(event_job_id):
        payload = getattr(event, "event_payload", None) or {}
        source = payload.get("source_document_id")
        target = payload.get("target_document_id")
        transition = payload.get("transition_type")
        if not (source and target and transition):
            continue
        pair = _pair_key(str(source), str(target), str(transition))
        if event.event_type in {LINEAGE_RECORDED_EVENT, LINEAGE_REFUSED_EVENT}:
            recorded.add(pair)
        elif event.event_type == LINEAGE_PENDING_EVENT:
            pending[pair] = payload
    for pair, payload in pending.items():
        if pair in recorded:
            continue
        await record_archive_lineage(
            archive_client=archive_client,
            ledger=ledger,
            event_job_id=event_job_id,
            source_document_id=str(payload["source_document_id"]),
            target_document_id=str(payload["target_document_id"]),
            transition_type=str(payload["transition_type"]),
            transition_reason=str(payload.get("transition_reason") or "lineage settlement"),
            caller_context=caller_context,
        )


class ReconciliationJobLedger(LineageEventLedger, Protocol):
    def get_job(self, job_id: str) -> Any: ...

    def list_pending_archive_lineage(self, *, limit: int) -> Sequence[Any]: ...


async def reconcile_pending_archive_lineage(
    *,
    archive_client: ArchiveLineageClient,
    ledger: ReconciliationJobLedger,
    limit: int,
) -> dict[str, int | float | None]:
    """One bounded pass over outstanding lineage pairs, oldest first.

    A transient Archive lifecycle outage self-heals after recovery even when
    nobody orders another correction: this pass re-attempts each pending
    pair through the same idempotent ``record_archive_lineage``, skips pairs
    already recorded or terminally refused, and leaves fresh transient
    failures pending for the next pass. No new lineage store exists - the
    durable job events remain the single source of truth.
    """

    outstanding = list(ledger.list_pending_archive_lineage(limit=limit))
    now = datetime.now(UTC)
    oldest_age_seconds: float | None = None
    for row in outstanding:
        created = row.oldest_created_at
        if created is None:
            continue
        age = (now - created).total_seconds()
        if oldest_age_seconds is None or age > oldest_age_seconds:
            oldest_age_seconds = age
    settled_jobs = 0
    for row in outstanding:
        job = ledger.get_job(str(row.job_id))
        caller_context = ReportCallerContext(
            trigger_type="system",
            triggered_by="archive-lineage-reconciler",
            caller_application="lotus-report",
            tenant_id=job.tenant_id,
            region=job.region,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        await settle_pending_archive_lineage(
            archive_client=archive_client,
            ledger=ledger,
            event_job_id=job.job_id,
            caller_context=caller_context,
        )
        settled_jobs += 1
    return {
        "outstanding_jobs": len(outstanding),
        "attempted_jobs": settled_jobs,
        "oldest_age_seconds": oldest_age_seconds,
    }
