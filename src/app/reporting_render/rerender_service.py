from __future__ import annotations

from functools import lru_cache
from time import perf_counter
from typing import Any, Protocol

from app.clients.archive_client import ArchiveClient
from app.clients.render_client import RenderClient
from app.config import settings
from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import (
    ReportCallerContext,
    ReportJobLedgerRecord,
    ReportJobRerenderRequest,
    ReportRerenderAttemptRecord,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_jobs.visibility import assert_job_visible
from app.reporting_lineage.service import get_report_input_snapshot_store
from app.reporting_lineage.store import ReportInputSnapshotNotFoundError
from app.reporting_metrics import record_report_operation
from app.reporting_render.package_builder import _build_render_package, _optional_int, _optional_str
from app.reporting_render.service import (
    _archive_failure_message,
    _archive_failure_posture,
    _build_archive_payload,
)


class RerenderSnapshotStore(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> Any: ...


class RerenderLedger(Protocol):
    def get_job(self, job_id: str) -> ReportJobLedgerRecord: ...

    def list_unresolved_archive_ambiguous_attempts(
        self, job_id: str
    ) -> list[ReportRerenderAttemptRecord]: ...

    def record_adopted_rerender_outcome(
        self,
        *,
        job: ReportJobLedgerRecord,
        idempotency_key: str,
        actor: str,
        reason: str,
        correlation_id: str,
        trace_id: str,
        adopted_attempt: ReportRerenderAttemptRecord,
        archive_document_id: str,
    ) -> ReportRerenderAttemptRecord: ...

    def create_rerender_attempt(
        self,
        *,
        job: ReportJobLedgerRecord,
        snapshot_id: str,
        snapshot_hash: str,
        idempotency_key: str,
        actor: str,
        reason: str,
        correlation_id: str,
        trace_id: str,
    ) -> tuple[ReportRerenderAttemptRecord, bool]: ...

    def mark_rerender_rendered(
        self,
        *,
        rerender_attempt_id: str,
        render_job_id: str,
        artifact_sha256: str | None,
        bounded_determinism_fingerprint: str | None,
        runtime_engine: str | None,
        runtime_engine_version: str | None,
        render_duration_ms: int | None,
    ) -> ReportRerenderAttemptRecord: ...

    def mark_rerender_archiving(
        self,
        *,
        rerender_attempt_id: str,
        archive_request_id: str,
    ) -> ReportRerenderAttemptRecord: ...

    def mark_rerender_archived(
        self,
        *,
        rerender_attempt_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        archive_document_id: str,
    ) -> ReportRerenderAttemptRecord: ...

    def mark_rerender_failed(
        self,
        *,
        rerender_attempt_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        failure_category: str,
        failure_message: str,
        retry_eligible: bool,
    ) -> ReportRerenderAttemptRecord: ...


class RerenderRenderClient(Protocol):
    async def submit_render_package(
        self,
        payload: dict[str, Any],
        *,
        correlation_id: str,
        trace_id: str,
    ) -> tuple[int, dict[str, Any]]: ...


class RerenderArchiveClient(Protocol):
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
    ) -> tuple[int, dict[str, Any]]: ...

    async def archive_document(
        self,
        payload: dict[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
        region: str,
        correlation_id: str,
        trace_id: str,
        booking_center_code: str | None = None,
        role: str | None = None,
    ) -> tuple[int, dict[str, Any]]: ...


class PortfolioReviewRerenderService:
    def __init__(
        self,
        *,
        render_client: RerenderRenderClient,
        archive_client: RerenderArchiveClient,
        snapshot_store: RerenderSnapshotStore,
        ledger: RerenderLedger,
    ) -> None:
        self._render_client = render_client
        self._archive_client = archive_client
        self._snapshot_store = snapshot_store
        self._ledger = ledger

    async def rerender_job(
        self,
        *,
        job_id: str,
        command: ReportJobRerenderRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportRerenderAttemptRecord:
        started_at = perf_counter()
        if not idempotency_key or not idempotency_key.strip():
            # Validated before the side-effecting resolution pass below, so a
            # rejected command performs no ledger writes.
            raise MissingIdempotencyKeyError("missing_idempotency_key")
        job = self._ledger.get_job(job_id)
        assert_job_visible(job, caller_context)
        _assert_rerender_eligible(job)
        try:
            snapshot = self._snapshot_store.get_snapshot_by_job(job_id)
        except ReportInputSnapshotNotFoundError as exc:
            raise ReportJobNotFoundError("report_snapshot_not_found") from exc

        resolved = await self._resolve_ambiguous_attempts(
            job=job,
            caller_context=caller_context,
            idempotency_key=idempotency_key,
            reason=command.reason,
        )
        if resolved is not None:
            record_report_operation(
                operation="rerender_from_snapshot",
                status=resolved.status,
                failure_category=resolved.failure_category,
                duration_seconds=perf_counter() - started_at,
            )
            return resolved

        attempt, created = self._ledger.create_rerender_attempt(
            job=job,
            snapshot_id=snapshot.snapshot_id,
            snapshot_hash=snapshot.snapshot_hash,
            idempotency_key=idempotency_key or "",
            actor=caller_context.triggered_by,
            reason=command.reason,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
        )
        if not created:
            return attempt

        payload = _build_render_package(
            job=job,
            snapshot=snapshot.snapshot_payload,
            render_job_id=attempt.render_job_id,
            snapshot_id=snapshot.snapshot_id,
        )
        status_code, render_response = await self._render_client.submit_render_package(
            payload,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
        )
        if status_code in {200, 201} and render_response.get("status") == "rendered":
            rendered = self._ledger.mark_rerender_rendered(
                rerender_attempt_id=attempt.rerender_attempt_id,
                render_job_id=str(render_response.get("render_job_id") or attempt.render_job_id),
                artifact_sha256=_optional_str(render_response.get("artifact_sha256")),
                bounded_determinism_fingerprint=_optional_str(
                    render_response.get("bounded_determinism_fingerprint")
                ),
                runtime_engine=_optional_str(render_response.get("runtime_engine")),
                runtime_engine_version=_optional_str(render_response.get("runtime_engine_version")),
                render_duration_ms=_optional_int(render_response.get("render_duration_ms")),
            )
            archived = await self._archive_rerendered_job(
                job=job,
                snapshot=snapshot,
                attempt=rendered,
                render_response=render_response,
                caller_context=caller_context,
            )
            record_report_operation(
                operation="rerender_from_snapshot",
                status=archived.status,
                failure_category=archived.failure_category,
                duration_seconds=perf_counter() - started_at,
            )
            return archived

        failure_category, retry_eligible = _render_failure_posture(status_code, render_response)
        failed = self._ledger.mark_rerender_failed(
            rerender_attempt_id=attempt.rerender_attempt_id,
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            failure_category=failure_category,
            failure_message=_render_failure_message(render_response),
            retry_eligible=retry_eligible,
        )
        record_report_operation(
            operation="rerender_from_snapshot",
            status=failed.status,
            failure_category=failed.failure_category,
            duration_seconds=perf_counter() - started_at,
        )
        return failed

    async def _resolve_ambiguous_attempts(
        self,
        *,
        job: ReportJobLedgerRecord,
        caller_context: ReportCallerContext,
        idempotency_key: str,
        reason: str,
    ) -> ReportRerenderAttemptRecord | None:
        """Resolution-first recovery for attempts (issue #215): an attempt
        that failed on the archive stage is ambiguous - its
        arch_{render_job_id} may have committed before the response was lost,
        and a fresh attempt would mint a new request id that archive
        idempotency cannot converge, duplicating the correction document.
        Each ambiguous attempt is resolved newest-first: a committed
        correction is ADOPTED as that attempt's outcome and returned as this
        request's result; an unanswerable lookup refuses fail-closed; only
        confirmed 404s across every ambiguous attempt permit a fresh one.
        """

        adopted_outcome: ReportRerenderAttemptRecord | None = None
        # Newest-first (the ledger query orders by updated_at DESC) and
        # unlimited: the newest committed correction becomes this request's
        # outcome, and EVERY remaining ambiguity is resolved in the same pass
        # so none can surface later as a stale adoption.
        for attempt in self._ledger.list_unresolved_archive_ambiguous_attempts(job.job_id):
            archive_request_id = f"arch_{attempt.render_job_id}"
            try:
                status_code, payload = await self._archive_client.get_document_by_request_id(
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
                raise InvalidReportJobTransitionError("report_job_cannot_be_rerendered") from exc
            document_id = _optional_str(payload.get("document_id")) if status_code == 200 else None
            if status_code == 200 and document_id:
                self._ledger.mark_rerender_archived(
                    rerender_attempt_id=attempt.rerender_attempt_id,
                    actor=caller_context.triggered_by,
                    correlation_id=caller_context.correlation_id,
                    trace_id=caller_context.trace_id,
                    archive_document_id=document_id,
                )
                if adopted_outcome is None:
                    adopted_outcome = self._ledger.record_adopted_rerender_outcome(
                        job=job,
                        idempotency_key=idempotency_key,
                        actor=caller_context.triggered_by,
                        reason=reason,
                        correlation_id=caller_context.correlation_id,
                        trace_id=caller_context.trace_id,
                        adopted_attempt=attempt,
                        archive_document_id=document_id,
                    )
                continue
            if status_code != 404:
                raise InvalidReportJobTransitionError("report_job_cannot_be_rerendered")
        return adopted_outcome

    async def _archive_rerendered_job(
        self,
        *,
        job: ReportJobLedgerRecord,
        snapshot: Any,
        attempt: ReportRerenderAttemptRecord,
        render_response: dict[str, Any],
        caller_context: ReportCallerContext,
    ) -> ReportRerenderAttemptRecord:
        artifact_base64 = _optional_str(render_response.get("artifact_base64"))
        if artifact_base64 is None:
            # Same recoverable posture as the primary orchestration: a replayed
            # "rendered" response carries no bytes, and a new rerender attempt
            # (fresh idempotency key, fresh render job id) regenerates them.
            return self._ledger.mark_rerender_failed(
                rerender_attempt_id=attempt.rerender_attempt_id,
                actor=caller_context.triggered_by,
                correlation_id=caller_context.correlation_id,
                trace_id=caller_context.trace_id,
                failure_category="render_artifact_unrecoverable",
                failure_message=(
                    "The render completed previously but its artifact was only "
                    "available in the original response. Request a new rerender "
                    "attempt to regenerate it from the retained snapshot."
                ),
                retry_eligible=True,
            )

        archive_request_id = f"arch_{attempt.render_job_id}"
        archiving = self._ledger.mark_rerender_archiving(
            rerender_attempt_id=attempt.rerender_attempt_id,
            archive_request_id=archive_request_id,
        )
        status_code, archive_response = await self._archive_client.archive_document(
            _build_archive_payload(
                job=job,
                snapshot=snapshot,
                render_response=render_response,
                archive_request_id=archive_request_id,
                content_base64=artifact_base64,
                render_attempt_id=attempt.rerender_attempt_id,
                supersedes_render_job_id=attempt.previous_render_job_id,
                supersedes_archive_document_id=attempt.previous_archive_document_id,
                archive_consequence="correction",
            ),
            actor_id=caller_context.triggered_by,
            tenant_id=caller_context.tenant_id,
            region=caller_context.region,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            booking_center_code=caller_context.booking_center_code,
            role=caller_context.role,
        )
        document_id = _optional_str(archive_response.get("document_id"))
        if status_code in {200, 201} and document_id:
            return self._ledger.mark_rerender_archived(
                rerender_attempt_id=attempt.rerender_attempt_id,
                actor=caller_context.triggered_by,
                correlation_id=caller_context.correlation_id,
                trace_id=caller_context.trace_id,
                archive_document_id=document_id,
            )

        failure_category, retry_eligible = _archive_failure_posture(
            status_code, archive_response, report_type=job.report_type
        )
        if failure_category in {"archive_storage_failed", "archive_execution_failed"}:
            # Retry-eligible for every family: attempt recovery is
            # resolution-first (this service resolves the failed attempt's
            # own arch_{render_job_id} before any new attempt), so a retry
            # adopts a committed correction instead of duplicating it.
            retry_eligible = True
        return self._ledger.mark_rerender_failed(
            rerender_attempt_id=archiving.rerender_attempt_id,
            actor=caller_context.triggered_by,
            correlation_id=caller_context.correlation_id,
            trace_id=caller_context.trace_id,
            failure_category=failure_category,
            failure_message=_archive_failure_message(archive_response),
            retry_eligible=retry_eligible,
        )


@lru_cache(maxsize=1)
def get_portfolio_review_rerender_service() -> PortfolioReviewRerenderService:
    return PortfolioReviewRerenderService(
        render_client=RenderClient(
            base_url=settings.render_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        ),
        archive_client=ArchiveClient(
            base_url=settings.archive_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        ),
        snapshot_store=get_report_input_snapshot_store(),
        ledger=get_report_job_ledger(),
    )


def _assert_rerender_eligible(job: ReportJobLedgerRecord) -> None:
    if (
        job.status != "archived"
        or "pdf" not in job.requested_output_formats
        or not job.render_job_id
        or not job.archive_document_id
    ):
        raise InvalidReportJobTransitionError("report_job_cannot_be_rerendered")


def _render_failure_posture(status_code: int, payload: dict[str, Any]) -> tuple[str, bool]:
    detail = payload.get("detail")
    detail_payload = detail if isinstance(detail, dict) else {}
    code = str(detail_payload.get("code") or "")
    if status_code == 409 or code == "render_job_conflict":
        return "render_conflict", False
    if status_code == 422 or code == "render_package_invalid":
        return "render_validation_failed", False
    return "render_execution_failed", status_code >= 500


def _render_failure_message(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return _optional_str(detail.get("message")) or "lotus-render execution failed."
    return (
        _optional_str(payload.get("failure_message"))
        or _optional_str(detail)
        or "lotus-render execution failed."
    )
