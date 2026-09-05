from __future__ import annotations

from functools import lru_cache
from time import perf_counter
from typing import Any, Protocol

from app.clients.render_client import RenderClient
from app.config import settings
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_report_input_snapshot_store
from app.reporting_metrics import record_report_operation
from app.reporting_render.document_reference import (
    derive_archive_request_id,
    mint_document_reference,
)
from app.reporting_render.package_builder import (
    _build_render_package,
    _job_template_identity,
    _optional_int,
    _optional_str,
)
from app.reporting_render.package_builder import (
    template_contract_mismatch as _template_contract_mismatch,
)


class RenderSnapshotStore(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> Any: ...


class RenderJobLedger(Protocol):
    def mark_rendering(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        render_job_id: str,
        output_format: str,
        template_id: str,
        template_version: str,
    ) -> ReportJobLedgerRecord: ...

    def mark_completed(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        render_job_id: str,
        output_format: str,
        template_id: str,
        template_version: str,
        template_publication: str | None,
        artifact_sha256: str | None,
        bounded_determinism_fingerprint: str | None,
        runtime_engine: str | None,
        runtime_engine_version: str | None,
        render_duration_ms: int | None,
    ) -> ReportJobLedgerRecord: ...

    def mark_archiving(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        archive_request_id: str,
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

    def mark_failed(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        failure_category: str,
        failure_message: str,
        retry_eligible: bool,
    ) -> ReportJobLedgerRecord: ...


class _WaitingOnRender:
    """Sentinel type: the persisted render is owner-side work in progress.
    The job stays NONTERMINAL and the work queue DEFERS - waiting is not
    failure, and the failure budget is scoped to real failures only
    (report#303)."""


WAITING_ON_RENDER = _WaitingOnRender()

#: The report#303 mapping table: Render's owner recovery vocabulary ->
#: (report failure_category, report retry_eligible). Two retryable
#: meanings are reconciled EXPLICITLY here: Render's retryable=True on
#: escalate_template_support means "retry can help AFTER remediation";
#: the queue's retry_eligible means "blind retry helps" - the stricter
#: queue semantic is a deliberate choice, never a silent shadowing.
#: wait_for_completion and read_artifact_metadata are not failures at all
#: (WAIT - the next resolution adopts); an unmapped value fails closed.
RENDER_RECOVERY_ACTION_MAP: dict[str, tuple[str, bool]] = {
    # resubmit_identical_package_or_escalate_runtime is deliberately ABSENT:
    # the owner's named remedy is handled structurally (a convergent
    # resubmission under the persisted render id), never as a failure.
    # The remaining rows are defensive completeness - Render's shipped
    # diagnostics emit only wait_for_completion or the stale resubmit
    # action for an in-progress render, so these map owner vocabulary that
    # cannot currently reach this branch; an unmapped value still fails
    # closed below.
    "fix_upstream_render_package": ("render_validation_failed", False),
    "fix_template_registry_or_package": ("render_validation_failed", False),
    "escalate_render_runtime": ("render_execution_failed", False),
    "escalate_template_support": ("render_execution_failed", False),
    "reduce_document_size_or_raise_envelope": ("render_validation_failed", False),
    "escalate_reporting_platform": ("render_execution_failed", False),
}

RENDER_RECOVERY_WAIT_ACTIONS = frozenset({"wait_for_completion", "read_artifact_metadata"})


class PortfolioReviewRenderOrchestrationService:
    def __init__(
        self,
        *,
        render_client: RenderClient,
        snapshot_store: RenderSnapshotStore,
        job_ledger: RenderJobLedger,
    ) -> None:
        self._render_client = render_client
        self._snapshot_store = snapshot_store
        self._job_ledger = job_ledger

    async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
        started_at = perf_counter()
        if "pdf" not in job.requested_output_formats:
            return job
        # One guard: terminal statuses (archived, completed_with_warnings,
        # failed, cancelled) and anything else non-actionable fall through
        # identically - only these four statuses have render work to do.
        if job.status not in {"data_ready", "rendering", "completed", "archiving"}:
            return job

        snapshot = self._snapshot_store.get_snapshot_by_job(job.job_id)
        render_job_id = job.render_job_id or f"rdr_{job.job_id}_pdf"
        if job.status in {"rendering", "completed", "archiving"}:
            # Resolution BEFORE any package recomposition: recovering an
            # existing render must never depend on this deployment still
            # being able to recompose the package - a completed v1 job
            # adopts its owner outcome even after the composer moved to v2.
            recovered = await self._recover_persisted_render(
                job=job,
                snapshot=snapshot,
                render_job_id=render_job_id,
                started_at=started_at,
            )
            if isinstance(recovered, ReportJobLedgerRecord):
                return recovered
            outcome = recovered
        else:
            built = self._build_package_or_fail(
                job=job,
                snapshot=snapshot,
                render_job_id=render_job_id,
                started_at=started_at,
            )
            if isinstance(built, ReportJobLedgerRecord):
                return built
            self._job_ledger.mark_rendering(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                render_job_id=render_job_id,
                output_format="pdf",
                template_id=str(built["template_id"]),
                template_version=str(built["template_version"]),
            )
            outcome = await self._render_client.submit_render_package(
                built,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        status_code, response_payload = outcome
        # The identity every outcome is validated against comes from the
        # JOB's persisted acceptance facts, never from a recomposed package.
        template_id, template_version = _job_template_identity(job)
        document_reference = mint_document_reference(
            report_job_id=job.job_id,
            snapshot_id=snapshot.snapshot_id,
            template_id=template_id,
            template_version=template_version,
        )
        if status_code in {200, 201} and response_payload.get("status") == "rendered":
            # The template Render used must equal what Report ordered - the
            # persisted acceptance fact the document_reference binds. A
            # response stating a different (or no) template identity rendered
            # a document this job never ordered: fail closed, never record it
            # as this job's completion.
            mismatch = _template_contract_mismatch(
                {"template_id": template_id, "template_version": template_version},
                response_payload,
            )
            if mismatch:
                failed_job = self._job_ledger.mark_failed(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    failure_category="render_validation_failed",
                    failure_message=mismatch,
                    retry_eligible=False,
                )
                record_report_operation(
                    operation="render_handoff",
                    status=failed_job.status,
                    failure_category=failed_job.failure_category,
                    duration_seconds=perf_counter() - started_at,
                )
                return failed_job
            rendered = job
            if job.status in {"data_ready", "rendering"}:
                rendered = self._job_ledger.mark_completed(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    render_job_id=str(response_payload.get("render_job_id") or render_job_id),
                    output_format="pdf",
                    template_id=template_id,
                    template_version=template_version,
                    template_publication=_optional_str(
                        response_payload.get("template_publication")
                    ),
                    artifact_sha256=_optional_str(response_payload.get("artifact_sha256")),
                    bounded_determinism_fingerprint=_optional_str(
                        response_payload.get("bounded_determinism_fingerprint")
                    ),
                    runtime_engine=_optional_str(response_payload.get("runtime_engine")),
                    runtime_engine_version=_optional_str(
                        response_payload.get("runtime_engine_version")
                    ),
                    render_duration_ms=_optional_int(response_payload.get("render_duration_ms")),
                )
            archived = self._record_archive_outcome(
                job=rendered,
                document_reference=document_reference,
                render_response=response_payload,
            )
            record_report_operation(
                operation="render_handoff",
                status=archived.status,
                failure_category=archived.failure_category,
                duration_seconds=perf_counter() - started_at,
            )
            return archived

        detail = response_payload.get("detail")
        detail_payload = detail if isinstance(detail, dict) else {}
        failure_code = str(detail_payload.get("code") or "")
        failure_message = _optional_str(detail_payload.get("message")) or _optional_str(
            response_payload.get("failure_message")
        )
        failure_category = "render_execution_failed"
        retry_eligible = status_code >= 500
        if status_code == 409 or failure_code == "render_job_conflict":
            failure_category = "render_conflict"
            retry_eligible = False
        elif status_code == 422 or failure_code == "render_package_invalid":
            failure_category = "render_validation_failed"
            retry_eligible = False
        failed_job = self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=failure_message or "lotus-render execution failed.",
            retry_eligible=retry_eligible,
        )
        record_report_operation(
            operation="render_handoff",
            status=failed_job.status,
            failure_category=failed_job.failure_category,
            duration_seconds=perf_counter() - started_at,
        )
        return failed_job

    async def _recover_persisted_render(
        self,
        *,
        job: ReportJobLedgerRecord,
        snapshot: Any,
        render_job_id: str,
        started_at: float,
    ) -> tuple[int, dict[str, Any]] | ReportJobLedgerRecord:
        """One recovered outcome (or terminal record) for a resumed job.

        Waiting returns the job UNCHANGED - nonterminal by design, so the
        queue defers without burning the failure budget and the eventual
        outcome is adopted under the SAME render id. A verified 404 while
        the ledger says rendering is the only path that composes and
        submits - the unsupported-accepted-contract refusal runs there and
        nowhere else during recovery.
        """

        resolution = await self._resolve_persisted_render(
            job=job,
            render_job_id=render_job_id,
            started_at=started_at,
        )
        if isinstance(resolution, _WaitingOnRender):
            return job
        if resolution is None:
            built = self._build_package_or_fail(
                job=job,
                snapshot=snapshot,
                render_job_id=render_job_id,
                started_at=started_at,
            )
            if isinstance(built, ReportJobLedgerRecord):
                return built
            submitted: tuple[int, dict[str, Any]] = await self._render_client.submit_render_package(
                built,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
            return submitted
        return resolution

    def _build_package_or_fail(
        self,
        *,
        job: ReportJobLedgerRecord,
        snapshot: Any,
        render_job_id: str,
        started_at: float,
    ) -> dict[str, Any] | ReportJobLedgerRecord:
        """Compose the render package for a genuinely NEW submission.

        Composition - including the unsupported-accepted-contract refusal -
        runs ONLY when a submission is actually needed; recovering an
        existing render never recomposes.
        """

        try:
            payload: dict[str, Any] = _build_render_package(
                job=job,
                snapshot=snapshot.snapshot_payload,
                render_job_id=render_job_id,
                # The durable record's identity - the payload does not carry
                # it, and governed rendering fails closed without it.
                snapshot_id=snapshot.snapshot_id,
                report_revision_id=snapshot.report_revision_id,
            )
        except ValueError as exc:
            failed_job = self._job_ledger.mark_failed(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                failure_category="render_validation_failed",
                failure_message=str(exc),
                retry_eligible=False,
            )
            record_report_operation(
                operation="render_handoff",
                status=failed_job.status,
                failure_category=failed_job.failure_category,
                duration_seconds=perf_counter() - started_at,
            )
            return failed_job
        return payload

    async def _resolve_persisted_render(
        self,
        *,
        job: ReportJobLedgerRecord,
        render_job_id: str,
        started_at: float,
    ) -> tuple[int, dict[str, Any]] | ReportJobLedgerRecord | _WaitingOnRender | None:
        """Resolve a persisted render before any resubmission.

        A rendered outcome is ADOPTED verbatim - the status projection
        carries every field the submit response does except the artifact
        bytes, which Report never consumes. A persisted FAILURE is adopted
        with Render's own failure vocabulary mapped explicitly, so a
        transient engine failure stays retryable across a worker restart.

        A render still in progress consults the OWNER's diagnostics
        contract: wait_for_completion keeps the job NONTERMINAL (the queue
        DEFERS without burning the failure budget - waiting is not
        failure) and the eventual outcome is adopted under the SAME render
        id; a stale or escalated posture maps through the explicit
        report#303 table into a REAL failure. An unanswerable lookup waits:
        marking the job failed would be terminal for the queue - only
        replay could act, and replay mints a fresh render id while the
        original can still finish and archive, a duplicate-document path.
        Returns WAITING_ON_RENDER to wait, None to authorize a fresh
        submission (verified 404 while the ledger says rendering), a
        terminal record, or the adopted (status, payload) outcome.

        A verified 404 means the original submission never landed - safe
        to submit - but ONLY while the job's own ledger agrees nothing
        completed (status ``rendering``). Local completion evidence
        (``completed``/``archiving``) outranks a 404: the render DID
        happen, so the job routes to the designed recovery categories
        instead of re-rendering different bytes into a duplicate document.
        The 404 reading RELIES on an owner fact: lotus-render's store has
        no purge or retention surface, so a 404 today genuinely means
        never-submitted, not expired. Render records this as a versioned
        consumer contract - a future retention feature must coordinate
        with Report before 404 can mean anything else.
        """

        lookup_status, lookup_payload = await self._render_client.get_render_status(
            render_job_id,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        if lookup_status == 200:
            render_status = lookup_payload.get("status")
            if render_status == "rendered":
                return 200, lookup_payload
            if render_status == "failed":
                return self._adopt_persisted_render_failure(
                    job=job,
                    lookup_payload=lookup_payload,
                    started_at=started_at,
                )
            return await self._escalate_or_wait(
                job=job,
                render_job_id=render_job_id,
                started_at=started_at,
            )
        if lookup_status == 404:
            if job.status == "rendering":
                return None
            return self._fail_completed_render_lost(job=job, started_at=started_at)
        return self._leave_resolution_pending(
            job=job,
            started_at=started_at,
            reason="render_resolution_unavailable",
        )

    async def _escalate_or_wait(
        self,
        *,
        job: ReportJobLedgerRecord,
        render_job_id: str,
        started_at: float,
    ) -> ReportJobLedgerRecord | _WaitingOnRender | None:
        """The owner decides whether in-progress means wait or escalate.

        Render's diagnostics contract states recovery_action against the
        OWNER's staleness thresholds; the report#303 mapping table converts
        the owner vocabulary into queue semantics, and an unmapped value
        fails closed naming itself rather than guessing wait semantics.
        An unanswerable diagnostics lookup waits - with no escalation
        channel, not duplicating a document outranks failing fast.
        """

        diag_status, diag_payload = await self._render_client.get_render_diagnostics(
            render_job_id,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        if diag_status != 200:
            return self._leave_resolution_pending(
                job=job,
                started_at=started_at,
                reason="render_resolution_in_progress",
            )
        recovery_action = _optional_str(diag_payload.get("recovery_action")) or ""
        if recovery_action == "resubmit_identical_package_or_escalate_runtime":
            # The owner's named remedy for a STALE in-progress render: an
            # identical resubmission under the SAME render id converges BY
            # CONSTRUCTION (create-or-get takeover), dead executor or merely
            # slow - construction over a threshold-sanity argument about
            # owner settings, and replay's fresh render id is never needed.
            # If this deployment's builder moved shape since the original
            # package, create-or-get refuses as render_conflict - loud to an
            # operator, never a silent duplicate. Ledger completion evidence
            # still outranks an owner in-progress anomaly.
            if job.status == "rendering":
                return None
            return self._fail_completed_render_lost(job=job, started_at=started_at)
        if recovery_action in RENDER_RECOVERY_WAIT_ACTIONS:
            return self._leave_resolution_pending(
                job=job,
                started_at=started_at,
                reason="render_resolution_in_progress",
            )
        mapped = RENDER_RECOVERY_ACTION_MAP.get(recovery_action)
        if mapped is None:
            failure_category, retry_eligible = "render_execution_failed", False
            failure_message = (
                "lotus-render stated recovery action "
                f"{recovery_action or 'absent'!r}, which this consumer does not "
                "map; failing closed rather than guessing wait semantics."
            )
        else:
            failure_category, retry_eligible = mapped
            support_message = _optional_str(diag_payload.get("support_message"))
            stale_state = _optional_str(diag_payload.get("stale_state"))
            failure_message = (
                f"lotus-render diagnostics escalated the persisted render: "
                f"recovery_action={recovery_action}"
                + (f", stale_state={stale_state}" if stale_state else "")
                + (f". {support_message}" if support_message else ".")
            )
        failed_job = self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=failure_message,
            retry_eligible=retry_eligible,
        )
        record_report_operation(
            operation="render_handoff",
            status=failed_job.status,
            failure_category=failed_job.failure_category,
            duration_seconds=perf_counter() - started_at,
        )
        return failed_job

    def _leave_resolution_pending(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
        reason: str,
    ) -> _WaitingOnRender:
        """Nonterminal by design: the work queue DEFERS the SAME job and
        render id without burning the failure budget, so the eventual
        terminal outcome is adopted rather than replayed under a fresh
        identity."""

        record_report_operation(
            operation="render_handoff",
            status=job.status,
            failure_category=reason,
            duration_seconds=perf_counter() - started_at,
        )
        return WAITING_ON_RENDER

    #: Render-owned failure categories that a live submission would have
    #: surfaced as retryable transport/engine trouble; everything else in
    #: the owner vocabulary is deterministic for the same package.
    _RETRYABLE_RENDER_FAILURE_CATEGORIES = frozenset({"engine_unavailable", "timeout"})
    _VALIDATION_RENDER_FAILURE_CATEGORIES = frozenset(
        {"package_validation_failed", "template_not_supported", "artifact_validation_failed"}
    )

    def _adopt_persisted_render_failure(
        self,
        *,
        job: ReportJobLedgerRecord,
        lookup_payload: dict[str, Any],
        started_at: float,
    ) -> ReportJobLedgerRecord:
        """Adopt a persisted render failure with its own vocabulary intact:
        the projection's 200 transport status must not overwrite what the
        failure actually was."""

        render_category = _optional_str(lookup_payload.get("failure_category")) or ""
        if render_category in self._VALIDATION_RENDER_FAILURE_CATEGORIES:
            failure_category = "render_validation_failed"
            retry_eligible = False
        else:
            failure_category = "render_execution_failed"
            retry_eligible = render_category in self._RETRYABLE_RENDER_FAILURE_CATEGORIES
        failure_message = _optional_str(lookup_payload.get("failure_message")) or (
            "lotus-render recorded the persisted render as failed"
            + (f" ({render_category})." if render_category else ".")
        )
        failed_job = self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=failure_message,
            retry_eligible=retry_eligible,
        )
        record_report_operation(
            operation="render_handoff",
            status=failed_job.status,
            failure_category=failed_job.failure_category,
            duration_seconds=perf_counter() - started_at,
        )
        return failed_job

    def _fail_completed_render_lost(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
    ) -> ReportJobLedgerRecord:
        """Local durable truth says this render completed; Render answering
        404 cannot un-happen it. Route to the recovery category whose
        replay semantics avoid a duplicate document: an archiving job's
        custody may already have committed (archive_outcome_unknown -
        replay resolves the recorded request id against Archive FIRST); a
        completed job's artifact is unrecoverable at Render
        (render_artifact_unrecoverable - replay clones the retained
        snapshot under a FRESH render id)."""

        if job.status == "archiving":
            failure_category = "archive_outcome_unknown"
            failure_message = (
                "lotus-render no longer holds the persisted render job while this "
                "job's custody outcome is unresolved; replay resolves the recorded "
                "archive request id against Archive before any re-render."
            )
        else:
            failure_category = "render_artifact_unrecoverable"
            failure_message = (
                "lotus-render no longer holds the persisted render job although this "
                "job durably recorded its completion; replay clones the retained "
                "snapshot and re-renders under a fresh render id."
            )
        failed_job = self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=failure_message,
            retry_eligible=True,
        )
        record_report_operation(
            operation="render_handoff",
            status=failed_job.status,
            failure_category=failed_job.failure_category,
            duration_seconds=perf_counter() - started_at,
        )
        return failed_job

    def _record_archive_outcome(
        self,
        *,
        job: ReportJobLedgerRecord,
        document_reference: str,
        render_response: dict[str, Any],
    ) -> ReportJobLedgerRecord:
        """The render#120 cutover: lotus-render is the ONE archive transmit
        authority. Report no longer relays bytes; it records the custody
        outcome Render reports and derives the reconciliation identity from
        facts it already holds (document_reference + artifact digest -> the
        same areq_ id Render derived). A job reaches "archived" ONLY on
        archived_verified with the durable document id - every other future
        fails closed with the request id recorded for reconciliation.
        """

        started_at = perf_counter()
        archive_state = _optional_str(render_response.get("archive_state"))
        document_id = _optional_str(render_response.get("archive_document_id"))
        artifact_sha256 = _optional_str(render_response.get("artifact_sha256"))
        # One authority for archive request identity (render#258): Render
        # derives the id, returns it, Report records it verbatim, Archive
        # resolves it. The local derivation remains ONLY as a rollout
        # fallback for responses predating the field, guarded by the
        # cross-repo parity test; it is deleted once the fallback is dead.
        archive_request_id = _optional_str(render_response.get("archive_request_id"))
        if archive_request_id is None and artifact_sha256:
            archive_request_id = derive_archive_request_id(document_reference, artifact_sha256)
        if archive_state == "archived_verified" and document_id and archive_request_id:
            if job.status == "completed":
                self._job_ledger.mark_archiving(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    archive_request_id=archive_request_id,
                )
            archived_job = self._job_ledger.mark_archived(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                archive_request_id=archive_request_id,
                archive_document_id=document_id,
            )
            record_report_operation(
                operation="archive_handoff",
                status=archived_job.status,
                duration_seconds=perf_counter() - started_at,
            )
            return archived_job

        # Recovery for a failed custody outcome is the RFC-0105 replay, whose
        # resolution-first pass looks up the recorded request id before any
        # re-render (re-rendered bytes are content-identical by fingerprint
        # but byte-different by design, so only the recorded id can converge
        # on what may have committed). That machinery exists for the
        # portfolio-review family only; other families stay non-retryable
        # rather than advertising a recovery that does not exist.
        resolvable = job.report_type == "portfolio_review"
        if archive_state == "archive_pending" and archive_request_id:
            # The delivery deadline expired after the request may have
            # committed. The derived request id is recorded durably FIRST so
            # reconciliation survives a crash, then the job fails closed.
            if job.status == "completed":
                self._job_ledger.mark_archiving(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    archive_request_id=archive_request_id,
                )
            failure_category = "archive_outcome_unknown"
            failure_message = (
                f"Archive custody is unresolved for {archive_request_id}: the "
                "handoff deadline expired and the delivery may have committed. "
                "Replay resolves this request id first - adopting a committed "
                "delivery or confirming a clean 404 - before any re-render."
            )
        elif archive_state == "archive_failed":
            # An exhausted 5xx sequence or a lost connection does not prove
            # Archive failed to commit. The delivery's request id is recorded
            # durably BEFORE the failure posture, so replay can resolve the
            # exact request that may have crossed the boundary.
            if archive_request_id and job.status == "completed":
                self._job_ledger.mark_archiving(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    archive_request_id=archive_request_id,
                )
            archive_detail = _optional_str(render_response.get("archive_detail")) or ""
            failure_category = "archive_handoff_failed"
            failure_message = (
                "lotus-render's archive handoff failed"
                + (f" for {archive_request_id}" if archive_request_id else "")
                + (f": {archive_detail}" if archive_detail else "")
            )
            # Archive's own words (render's stable grammar): a 4xx refusal
            # replays identically - the same declaration re-fails - so it is
            # terminal for every family until an operator acts.
            if _is_terminal_archive_refusal(archive_detail):
                return self._fail_archive_outcome(
                    job=job,
                    failure_category=failure_category,
                    failure_message=failure_message,
                    retry_eligible=False,
                    started_at=started_at,
                )
        else:
            # No archive handoff applied. Since the byte relay is retired,
            # a null archive_state is a configuration error (lotus-render's
            # LOTUS_RENDER_ARCHIVE_BASE_URL is unset or the response is
            # malformed) - never a silently unarchived document.
            failure_category = "archive_handoff_not_configured"
            failure_message = (
                "The render completed but no archive handoff applied "
                f"(archive_state={archive_state!r}). Report no longer relays "
                "bytes; configure lotus-render's archive handoff and retry."
            )
        return self._fail_archive_outcome(
            job=job,
            failure_category=failure_category,
            failure_message=failure_message,
            retry_eligible=resolvable,
            started_at=started_at,
        )

    def _fail_archive_outcome(
        self,
        *,
        job: ReportJobLedgerRecord,
        failure_category: str,
        failure_message: str,
        retry_eligible: bool,
        started_at: float,
    ) -> ReportJobLedgerRecord:
        failed_job = self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=failure_message,
            retry_eligible=retry_eligible,
        )
        record_report_operation(
            operation="archive_handoff",
            status=failed_job.status,
            failure_category=failed_job.failure_category,
            duration_seconds=perf_counter() - started_at,
        )
        return failed_job


def _is_terminal_archive_refusal(archive_detail: str) -> bool:
    """Archive refused custody with a 4xx (render's grammar:
    "archive_refused_<status>: <code>: <message>"). Deterministic re-renders
    redeliver identical bytes and the same declaration, so the refusal
    replays identically - retrying cannot succeed."""

    if not archive_detail.startswith("archive_refused_"):
        return False
    status_text = archive_detail.removeprefix("archive_refused_")[:3]
    return status_text.startswith("4")


@lru_cache(maxsize=1)
def get_portfolio_review_render_orchestration_service() -> (
    PortfolioReviewRenderOrchestrationService
):
    return PortfolioReviewRenderOrchestrationService(
        render_client=RenderClient(
            base_url=settings.render_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        ),
        snapshot_store=get_report_input_snapshot_store(),
        job_ledger=get_report_job_ledger(),
    )
