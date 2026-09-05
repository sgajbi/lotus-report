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
from app.reporting_render.document_reference import derive_archive_request_id
from app.reporting_render.package_builder import (
    _build_render_package,
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
        if job.status in {"archived", "completed_with_warnings", "failed", "cancelled"}:
            return job
        if job.status not in {"data_ready", "rendering", "completed", "archiving"}:
            return job

        snapshot = self._snapshot_store.get_snapshot_by_job(job.job_id)
        render_job_id = job.render_job_id or f"rdr_{job.job_id}_pdf"
        try:
            payload = _build_render_package(
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
        if job.status == "data_ready":
            self._job_ledger.mark_rendering(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                render_job_id=render_job_id,
                output_format="pdf",
                template_id=str(payload["template_id"]),
                template_version=str(payload["template_version"]),
            )

        status_code, response_payload = await self._render_client.submit_render_package(
            payload,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        if status_code in {200, 201} and response_payload.get("status") == "rendered":
            # The template Render used must equal what Report ordered - the
            # persisted acceptance fact the document_reference binds. A
            # response stating a different (or no) template identity rendered
            # a document this job never ordered: fail closed, never record it
            # as this job's completion.
            mismatch = _template_contract_mismatch(payload, response_payload)
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
                    template_id=str(payload["template_id"]),
                    template_version=str(payload["template_version"]),
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
                package=payload,
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

    def _record_archive_outcome(
        self,
        *,
        job: ReportJobLedgerRecord,
        package: dict[str, Any],
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
            reference = str(package["render_context"]["document_reference"])
            archive_request_id = derive_archive_request_id(reference, artifact_sha256)
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
