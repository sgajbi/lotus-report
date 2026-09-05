from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.clients.archive_client import ArchiveClient
from app.config import settings
from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobLedgerRecord,
    ReportJobRelationshipRecord,
    ReportJobRelationshipType,
    ReportJobReplayRequest,
    ReportStatusEvent,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_jobs.visibility import assert_job_visible
from app.reporting_lineage.service import (
    get_portfolio_review_snapshot_capture_service,
    get_report_input_snapshot_store,
)
from app.reporting_lineage.store import (
    ReportInputSnapshotCreateRequest,
    ReportInputSnapshotNotFoundError,
    ReportInputSnapshotRecord,
)
from app.reporting_metrics import (
    record_replay_fingerprint_comparison,
    record_report_operation,
)
from app.reporting_render.service import (
    RenderWaiting,
    get_portfolio_review_render_orchestration_service,
)


@dataclass(frozen=True)
class ReportReplayResult:
    source_job: ReportJobLedgerRecord
    replayed_job: ReportJobLedgerRecord
    idempotency_key: str


class ReplayLedger(Protocol):
    def get_job(self, job_id: str) -> ReportJobLedgerRecord: ...

    def create_replay_derived_job(
        self,
        *,
        source_job_id: str,
        request: PortfolioReviewJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
        reason: str,
    ) -> ReportJobLedgerRecord: ...

    def append_job_event(
        self,
        *,
        job_id: str,
        event_type: str,
        message: str,
        event_payload: dict[str, object] | None = None,
        event_idempotency_key: str | None = None,
        actor: str,
        correlation_id: str,
        trace_id: str,
        skip_if_idempotency_key_exists: bool = False,
    ) -> bool: ...

    def list_status_events(self, job_id: str) -> list[ReportStatusEvent]: ...

    def mark_collecting_data(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord: ...

    def mark_archived(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        archive_request_id: str,
        archive_document_id: str,
    ) -> ReportJobLedgerRecord: ...

    def mark_data_ready(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord: ...

    def upsert_job_relationship(
        self,
        *,
        source_job: ReportJobLedgerRecord,
        derived_job: ReportJobLedgerRecord,
        relationship_type: ReportJobRelationshipType,
        actor: str,
        reason: str,
        archive_consequence: str | None = None,
        previous_archive_document_id: str | None = None,
        new_archive_document_id: str | None = None,
    ) -> ReportJobRelationshipRecord: ...


class ReplayEventLedger(Protocol):
    def list_status_events(self, job_id: str) -> list[ReportStatusEvent]: ...

    def append_job_event(
        self,
        *,
        job_id: str,
        event_type: str,
        message: str,
        event_payload: dict[str, object] | None = None,
        event_idempotency_key: str | None = None,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> bool: ...


class ReplayCaptureService(Protocol):
    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


class ReplayArchiveResolver(Protocol):
    async def get_document_by_request_id(
        self,
        archive_request_id: str,
        *,
        actor_id: str,
        tenant_id: str,
        region: str,
        correlation_id: str,
        trace_id: str,
        booking_center_code: str | None = None,
        role: str | None = None,
    ) -> tuple[int, dict[str, object]]: ...


class ReplaySnapshotStore(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> ReportInputSnapshotRecord: ...

    def get_stored_lifecycle(self, snapshot_id: str) -> dict[str, Any] | None: ...

    def create_snapshot(
        self, request: ReportInputSnapshotCreateRequest
    ) -> ReportInputSnapshotRecord: ...


class ReplayRenderService(Protocol):
    async def render_for_job(
        self, job: ReportJobLedgerRecord
    ) -> "ReportJobLedgerRecord | RenderWaiting": ...


class PortfolioReviewReplayService:
    def __init__(
        self,
        *,
        ledger: ReplayLedger,
        capture_service: ReplayCaptureService,
        render_service: ReplayRenderService,
        snapshot_store: ReplaySnapshotStore | None = None,
        archive_resolver: ReplayArchiveResolver | None = None,
    ) -> None:
        self._ledger = ledger
        self._capture_service = capture_service
        self._render_service = render_service
        self._snapshot_store = snapshot_store
        self._archive_resolver = archive_resolver

    async def replay_job(
        self,
        *,
        job_id: str,
        command: ReportJobReplayRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportReplayResult:
        source_job = self._ledger.get_job(job_id)
        assert_job_visible(source_job, caller_context)
        assert_replay_eligible(source_job)
        replay_key = replay_idempotency_key(
            source_job_id=source_job.job_id,
            idempotency_key=idempotency_key,
        )
        # The resolver durably mutates the source job when it adopts a
        # committed document, so every pure validation runs before it.
        await self._resolve_archive_ambiguity(source_job=source_job, caller_context=caller_context)
        # The failed_work_replay relationship is written INSIDE the creation
        # transaction (with the source row locked on PostgreSQL), so a
        # concurrent novel-key replay serializes and then sees it - the
        # one-replacement guarantee holds under concurrency, not just
        # sequentially.
        replayed = self._ledger.create_replay_derived_job(
            source_job_id=source_job.job_id,
            request=portfolio_review_request_from_job(source_job),
            caller_context=caller_context,
            idempotency_key=replay_key,
            reason=command.reason,
        )
        if replayed.status in {"accepted", "collecting_data"}:
            # The retained snapshot is required only while collection still has
            # to happen; a same-key retry of an already completed replay stays
            # idempotent even after the source snapshot is retention-purged.
            source_snapshot = self._require_retained_snapshot(source_job)
            if replayed.status == "accepted":
                self._append_replay_requested_event(
                    source_job=source_job,
                    replayed=replayed,
                    reason=command.reason,
                    replay_key=replay_key,
                    caller_context=caller_context,
                )
            replayed = await self._collect_replay_inputs(
                source_snapshot=source_snapshot,
                source_job=source_job,
                replayed=replayed,
                caller_context=caller_context,
            )
        if replayed.status == "data_ready" and "pdf" in replayed.requested_output_formats:
            rendered = await self._render_service.render_for_job(replayed)
            # An owner-side wait surfaces the job unchanged: the operator
            # API answers with the nonterminal state and the work queue
            # keeps polling toward adoption.
            replayed = rendered.job if isinstance(rendered, RenderWaiting) else rendered
        if "pdf" in replayed.requested_output_formats:
            # Runs on fresh renders AND on same-key retries of an already
            # terminal replay: a crash between the durable render and the
            # event write must not leave the comparison permanently absent.
            # The recorder itself is idempotent and fingerprint-guarded.
            self._record_fingerprint_comparison(
                source_job=source_job,
                replayed=replayed,
                caller_context=caller_context,
            )
        if replayed.status in {
            "data_ready",
            "completed",
            "completed_with_warnings",
            "archived",
            "failed",
        }:
            append_source_event_once(
                self._ledger,
                job_id=source_job.job_id,
                event_type="job_replay_completed",
                replayed_job_id=replayed.job_id,
                replayed_status=replayed.status,
                message=(
                    f"Report replay completed as {replayed.job_id} with status {replayed.status}."
                ),
                caller_context=caller_context,
            )
        _upsert_replay_relationship(
            self._ledger,
            source_job=source_job,
            replayed=replayed,
            reason=command.reason,
            actor=caller_context.triggered_by,
        )
        record_report_operation(
            operation="replay_command",
            status=replayed.status,
            failure_category=replayed.failure_category,
        )
        return ReportReplayResult(
            source_job=source_job,
            replayed_job=replayed,
            idempotency_key=idempotency_key or "",
        )

    def _append_replay_requested_event(
        self,
        *,
        source_job: ReportJobLedgerRecord,
        replayed: ReportJobLedgerRecord,
        reason: str,
        replay_key: str,
        caller_context: ReportCallerContext,
    ) -> None:
        self._ledger.append_job_event(
            job_id=source_job.job_id,
            event_type="job_replay_requested",
            message=f"Report replay requested as {replayed.job_id}: {reason}",
            event_payload={"replayed_job_id": replayed.job_id},
            event_idempotency_key=replay_key,
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
        )

    def _record_fingerprint_comparison(
        self,
        *,
        source_job: ReportJobLedgerRecord,
        replayed: ReportJobLedgerRecord,
        caller_context: ReportCallerContext,
    ) -> None:
        """Observational determinism check (issue #202): a replay of a lost
        artifact SHOULD reproduce the source's bounded-determinism
        fingerprint when both renders ran the same governed runtime.
        Divergence is recorded, never failed - measured caveats (crossed
        runtimes look identical by version alone; typst#6783 can swap font
        sections) mean the event is a lead for operators, not a verdict."""

        # The comparison applies to every source that rendered before its
        # failure - the artifactless legacy posture and the custody-outcome
        # postures alike - because the replayed render is judged against the
        # source's persisted fingerprint, not against its archive fate.
        if source_job.failure_category not in {
            "render_artifact_unrecoverable",
            "archive_outcome_unknown",
            "archive_handoff_failed",
            "archive_handoff_not_configured",
        }:
            return
        rendered = replayed.status in {
            "completed",
            "completed_with_warnings",
            "archiving",
            "archived",
        } or any(
            event.event_type == "job_completed"
            for event in self._ledger.list_status_events(replayed.job_id)
        )
        if not rendered:
            # Durable completion truth: the render either left the job in a
            # rendered state or appended its job_completed lifecycle event
            # (which survives a later archive-leg failure). Optional response
            # metadata proves nothing - a valid render response may omit all
            # of it. A render that never completed stays silent; the job's
            # own failure posture tells that story.
            return
        outcome, reason = _fingerprint_outcome(source_job=source_job, replayed=replayed)
        appended = self._ledger.append_job_event(
            job_id=replayed.job_id,
            event_type="job_replay_fingerprint_compared",
            message=(
                f"Replay fingerprint comparison against {source_job.job_id}: {outcome}"
                + (f" ({reason})." if reason else ".")
            ),
            event_payload={
                "outcome": outcome,
                "reason": reason,
                "source_report_job_id": source_job.job_id,
                "source_fingerprint": source_job.render_bounded_determinism_fingerprint,
                "replayed_fingerprint": replayed.render_bounded_determinism_fingerprint,
                "source_runtime_engine": source_job.render_runtime_engine,
                "source_runtime_engine_version": source_job.render_runtime_engine_version,
                "replayed_runtime_engine": replayed.render_runtime_engine,
                "replayed_runtime_engine_version": replayed.render_runtime_engine_version,
            },
            event_idempotency_key=f"job_replay_fingerprint_compared:{replayed.job_id}",
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            skip_if_idempotency_key_exists=True,
        )
        if appended:
            # One increment per durable comparison event: routine same-key
            # HTTP retries of a terminal replay must not inflate the outcome
            # totals or any derived divergence rate.
            record_replay_fingerprint_comparison(outcome=outcome, reason=reason)

    async def _resolve_archive_ambiguity(
        self,
        *,
        source_job: ReportJobLedgerRecord,
        caller_context: ReportCallerContext,
    ) -> None:
        """A retryable archive-stage failure is ambiguous: archive may have
        committed the document before the response was lost. Replaying would
        mint a fresh arch_{render_job_id} that archive idempotency cannot
        converge with the committed one - a duplicate client document. So the
        original request id is resolved first: a committed document is
        ADOPTED (the source job becomes archived - the truthful terminal
        state) and the replay refuses as unnecessary; an unresolvable lookup
        refuses fail-closed rather than risking the duplicate; only a
        confirmed 404 lets the replay proceed.
        """

        if source_job.failure_category not in {
            "archive_storage_failed",
            "archive_execution_failed",
            "archive_outcome_unknown",
            "archive_handoff_failed",
        }:
            return
        if self._archive_resolver is None or not source_job.render_job_id:
            raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")
        if source_job.failure_category in {"archive_outcome_unknown", "archive_handoff_failed"}:
            # Post-cutover custody failures record the derived areq_ id at
            # failure time. Without it the original request is unresolvable
            # and rendering a replacement could duplicate a committed
            # document - fail closed rather than guess.
            archive_request_id = source_job.archive_request_id
            if not archive_request_id:
                raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")
        else:
            # Pre-cutover rows fall back to the historical arch_ scheme.
            archive_request_id = source_job.archive_request_id or f"arch_{source_job.render_job_id}"
        try:
            status_code, payload = await self._archive_resolver.get_document_by_request_id(
                archive_request_id,
                actor_id=caller_context.triggered_by,
                tenant_id=caller_context.tenant_id,
                region=caller_context.region,
                correlation_id=caller_context.correlation_id,
                trace_id=caller_context.trace_id,
                booking_center_code=caller_context.booking_center_code,
                role=caller_context.role,
            )
        except Exception as exc:
            raise InvalidReportJobTransitionError("report_job_cannot_be_replayed") from exc
        if status_code == 404:
            return
        document_id = str(payload.get("document_id") or "") if status_code == 200 else ""
        if status_code != 200 or not document_id:
            raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")
        resolved = self._ledger.mark_archived(
            job_id=source_job.job_id,
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            archive_request_id=archive_request_id,
            archive_document_id=document_id,
        )
        self._ledger.append_job_event(
            job_id=resolved.job_id,
            event_type="job_replay_archive_resolved",
            message=(
                f"Archive lookup resolved {archive_request_id} to committed document "
                f"{document_id}; the failure was a transport artifact and no replay is needed."
            ),
            event_payload={
                "archive_document_id": document_id,
                "archive_request_id": archive_request_id,
            },
            event_idempotency_key=f"job_replay_archive_resolved:{resolved.job_id}",
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            skip_if_idempotency_key_exists=True,
        )
        raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")

    def _require_retained_snapshot(
        self, source_job: ReportJobLedgerRecord
    ) -> ReportInputSnapshotRecord | None:
        # These postures promise recovery from the retained snapshot: the
        # source rendered (or renders deterministically) from evidence that
        # is already captured, so recollecting current upstream state would
        # silently produce a document with different evidence under a
        # failed-work-replay relationship. If the snapshot no longer exists
        # the replay is refused instead - before any replayed job is created.
        if (
            source_job.failure_category
            not in {
                "render_artifact_unrecoverable",
                "archive_outcome_unknown",
                "archive_handoff_failed",
                "archive_handoff_not_configured",
            }
            or self._snapshot_store is None
        ):
            return None
        try:
            return self._snapshot_store.get_snapshot_by_job(source_job.job_id)
        except ReportInputSnapshotNotFoundError as exc:
            raise InvalidReportJobTransitionError("report_job_cannot_be_replayed") from exc

    async def _collect_replay_inputs(
        self,
        *,
        source_snapshot: ReportInputSnapshotRecord | None,
        source_job: ReportJobLedgerRecord,
        replayed: ReportJobLedgerRecord,
        caller_context: ReportCallerContext,
    ) -> ReportJobLedgerRecord:
        if source_snapshot is not None:
            return self._clone_retained_snapshot(
                source_snapshot=source_snapshot,
                source_job=source_job,
                replayed=replayed,
                caller_context=caller_context,
            )
        return await self._capture_service.capture_for_job(replayed)

    def _clone_retained_snapshot(
        self,
        *,
        source_snapshot: ReportInputSnapshotRecord,
        source_job: ReportJobLedgerRecord,
        replayed: ReportJobLedgerRecord,
        caller_context: ReportCallerContext,
    ) -> ReportJobLedgerRecord:
        # An artifactless-replay failure happened after a successful capture,
        # so the source snapshot is the validated as-of truth for this report
        # and reusing it keeps the recovery deterministic even when upstream
        # state has moved on.
        assert self._snapshot_store is not None
        if replayed.status == "collecting_data":
            job = replayed
        else:
            job = self._ledger.mark_collecting_data(
                job_id=replayed.job_id,
                actor=caller_context.triggered_by,
                correlation_id=caller_context.correlation_id,
                trace_id=caller_context.trace_id,
            )
        try:
            cloned_snapshot = self._snapshot_store.get_snapshot_by_job(job.job_id)
        except ReportInputSnapshotNotFoundError:
            cloned_snapshot = self._snapshot_store.create_snapshot(
                ReportInputSnapshotCreateRequest(
                    report_job_id=job.job_id,
                    report_type=source_snapshot.report_type,
                    report_data_contract_version=source_snapshot.report_data_contract_version,
                    portfolio_scope=source_snapshot.portfolio_scope,
                    as_of_date=source_snapshot.as_of_date,
                    snapshot_payload=source_snapshot.snapshot_payload,
                    snapshot_storage_ref=source_snapshot.snapshot_storage_ref,
                    # A clone re-serves the SAME captured facts, so it IS the
                    # same report revision: the binding is inherited verbatim,
                    # NULLs included - a pre-identity source snapshot stays
                    # unlabelled rather than gaining an identity its original
                    # never stated.
                    report_revision_id=source_snapshot.report_revision_id,
                    series_digest=source_snapshot.series_digest,
                    source_revision_digest=source_snapshot.source_revision_digest,
                    factual_content_digest=source_snapshot.factual_content_digest,
                    factual_boundary_version=source_snapshot.factual_boundary_version,
                    source_revision_vector=source_snapshot.source_revision_vector,
                    source_cut_coherence=source_snapshot.source_cut_coherence,
                    # Inherited from the STORED bytes, not the translated
                    # record - a clone of a policy 1.0.0 source persists the
                    # 1.0.0 claim verbatim (readers translate at the store's
                    # read boundary), never a version/value pair no policy
                    # ever stamped.
                    lifecycle=self._snapshot_store.get_stored_lifecycle(
                        source_snapshot.snapshot_id
                    ),
                    supportability_status=source_snapshot.supportability_status,
                    completeness_status=source_snapshot.completeness_status,
                    lineage_summary=_cloned_lineage_summary(
                        source_snapshot=source_snapshot,
                        source_job_id=source_job.job_id,
                    ),
                    captured_at=datetime.now(UTC),
                    correlation_id=caller_context.correlation_id,
                    trace_id=caller_context.trace_id,
                )
            )
        self._append_snapshot_cloned_event(
            job=job,
            source_job=source_job,
            source_snapshot=source_snapshot,
            cloned_snapshot_id=cloned_snapshot.snapshot_id,
            caller_context=caller_context,
        )
        return self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
        )

    def _append_snapshot_cloned_event(
        self,
        *,
        job: ReportJobLedgerRecord,
        source_job: ReportJobLedgerRecord,
        source_snapshot: ReportInputSnapshotRecord,
        cloned_snapshot_id: str,
        caller_context: ReportCallerContext,
    ) -> None:
        self._ledger.append_job_event(
            job_id=job.job_id,
            event_type="job_replay_snapshot_cloned",
            message=(
                f"Replay reused retained input snapshot {source_snapshot.snapshot_id} "
                f"from {source_job.job_id} without recollecting upstream data."
            ),
            event_payload={
                "source_snapshot_id": source_snapshot.snapshot_id,
                "cloned_snapshot_id": cloned_snapshot_id,
                "replayed_job_id": job.job_id,
            },
            event_idempotency_key=f"job_replay_snapshot_cloned:{job.job_id}",
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            skip_if_idempotency_key_exists=True,
        )


def _fingerprint_outcome(
    *,
    source_job: ReportJobLedgerRecord,
    replayed: ReportJobLedgerRecord,
) -> tuple[str, str | None]:
    if replayed.render_bounded_determinism_fingerprint is None:
        return "incomparable", "replayed_fingerprint_missing"
    if source_job.render_bounded_determinism_fingerprint is None:
        return "incomparable", "source_fingerprint_missing"
    if not all(
        (
            source_job.render_runtime_engine,
            source_job.render_runtime_engine_version,
            replayed.render_runtime_engine,
            replayed.render_runtime_engine_version,
        )
    ):
        # Absent runtime identity must not compare equal as None == None: a
        # match claim requires proof both renders ran the same governed
        # runtime.
        return "incomparable", "runtime_identity_missing"
    if (
        source_job.render_runtime_engine != replayed.render_runtime_engine
        or source_job.render_runtime_engine_version != replayed.render_runtime_engine_version
    ):
        return "incomparable", "runtime_engine_differs"
    if (
        source_job.render_bounded_determinism_fingerprint
        == replayed.render_bounded_determinism_fingerprint
    ):
        return "matched", None
    return "diverged", "same_runtime_fingerprint_mismatch"


def _cloned_lineage_summary(
    *,
    source_snapshot: ReportInputSnapshotRecord,
    source_job_id: str,
) -> dict[str, object]:
    """Lineage for a cloned snapshot: no upstream calls were made for this
    job, so every per-snapshot call counter must be zero (the lineage read
    joins calls by snapshot id and would otherwise contradict the summary).
    The data's service provenance and captured posture stay, and the source
    snapshot id names where the original call evidence lives."""

    source_summary = source_snapshot.lineage_summary
    if source_summary.get("upstream_evidence") == "cloned_from_source_snapshot":
        # Chained recovery: the source snapshot is itself a clone with no call
        # rows of its own. Keep pointing at the root snapshot that actually
        # holds the upstream-call evidence, or audit navigation dead-ends at
        # an intermediate clone.
        evidence_snapshot_id = str(
            source_summary.get("cloned_from_snapshot_id") or source_snapshot.snapshot_id
        )
        evidence_report_job_id = str(
            source_summary.get("cloned_from_report_job_id") or source_job_id
        )
        source_call_count = source_summary.get("source_call_count", 0)
    else:
        evidence_snapshot_id = source_snapshot.snapshot_id
        evidence_report_job_id = source_job_id
        source_call_count = source_summary.get("call_count", 0)
    summary = dict(source_summary)
    summary.update(
        {
            "call_count": 0,
            "partial_call_count": 0,
            "unavailable_call_count": 0,
            "not_supported_call_count": 0,
            "redacted_call_count": 0,
            "upstream_evidence": "cloned_from_source_snapshot",
            "source_call_count": source_call_count,
            "cloned_from_report_job_id": evidence_report_job_id,
            "cloned_from_snapshot_id": evidence_snapshot_id,
        }
    )
    return summary


def get_portfolio_review_replay_service() -> PortfolioReviewReplayService:
    return PortfolioReviewReplayService(
        ledger=get_report_job_ledger(),
        capture_service=get_portfolio_review_snapshot_capture_service(),
        render_service=get_portfolio_review_render_orchestration_service(),
        snapshot_store=get_report_input_snapshot_store(),
        archive_resolver=ArchiveClient(
            base_url=settings.archive_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        ),
    )


def assert_replay_eligible(job: ReportJobLedgerRecord) -> None:
    # The replay command recreates a portfolio-review order; replaying any
    # other report type here would silently morph it into a portfolio review.
    # Other report families recover by resubmitting their own order, which is
    # equally deterministic because their render packages build from the
    # retained job request.
    if job.report_type != "portfolio_review":
        raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")
    if job.status != "failed" or not job.retry_eligible:
        raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")
    if job.archive_document_id:
        raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")


def replay_idempotency_key(*, source_job_id: str, idempotency_key: str | None) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise MissingIdempotencyKeyError("missing_idempotency_key")
    return f"replay:{source_job_id}:{idempotency_key.strip()}"


def portfolio_review_request_from_job(job: ReportJobLedgerRecord) -> PortfolioReviewJobRequest:
    return PortfolioReviewJobRequest(
        portfolio_scope=job.portfolio_scope,
        as_of_date=job.as_of_date,
        requested_output_formats=job.requested_output_formats,
        reporting_currency=job.reporting_currency,
        options=job.options,
    )


def append_source_event_once(
    ledger: ReplayEventLedger,
    *,
    job_id: str,
    event_type: str,
    replayed_job_id: str,
    replayed_status: str,
    message: str,
    caller_context: ReportCallerContext,
    event_payload: dict[str, object] | None = None,
) -> None:
    if any(
        event.event_type == event_type
        and event.event_payload.get("replayed_job_id") == replayed_job_id
        for event in ledger.list_status_events(job_id)
    ):
        return
    payload = {
        "replayed_job_id": replayed_job_id,
        "replayed_status": replayed_status,
        **(event_payload or {}),
    }
    ledger.append_job_event(
        job_id=job_id,
        event_type=event_type,
        message=message,
        event_payload=payload,
        event_idempotency_key=f"{event_type}:{job_id}:{replayed_job_id}",
        actor=caller_context.triggered_by,
        correlation_id=caller_context.correlation_id,
        trace_id=caller_context.trace_id,
    )


def _upsert_replay_relationship(
    ledger: ReplayLedger,
    *,
    source_job: ReportJobLedgerRecord,
    replayed: ReportJobLedgerRecord,
    reason: str,
    actor: str,
) -> None:
    ledger.upsert_job_relationship(
        source_job=source_job,
        derived_job=replayed,
        relationship_type="failed_work_replay",
        actor=actor,
        reason=reason,
        new_archive_document_id=replayed.archive_document_id,
    )
