from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from time import perf_counter
from typing import Any, Protocol

from app.clients.archive_client import ArchiveClient
from app.clients.render_client import RenderClient
from app.config import settings
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_report_input_snapshot_store
from app.reporting_metrics import record_report_operation
from app.reporting_render.package_builder import (
    _as_dict,
    _build_render_package,
    _optional_int,
    _optional_str,
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


class RenderArchiveClient(Protocol):
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


class PortfolioReviewRenderOrchestrationService:
    def __init__(
        self,
        *,
        render_client: RenderClient,
        archive_client: RenderArchiveClient,
        snapshot_store: RenderSnapshotStore,
        job_ledger: RenderJobLedger,
    ) -> None:
        self._render_client = render_client
        self._archive_client = archive_client
        self._snapshot_store = snapshot_store
        self._job_ledger = job_ledger

    async def render_for_job(
        self,
        job: ReportJobLedgerRecord,
        *,
        supersedes_render_job_id: str | None = None,
        supersedes_archive_document_id: str | None = None,
        archive_consequence: str | None = None,
    ) -> ReportJobLedgerRecord:
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
                template_id=str(payload.get("template_id") or "portfolio-review"),
                template_version=str(payload.get("template_version") or "v1"),
            )

        status_code, response_payload = await self._render_client.submit_render_package(
            payload,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        if status_code in {200, 201} and response_payload.get("status") == "rendered":
            rendered = job
            if job.status in {"data_ready", "rendering"}:
                rendered = self._job_ledger.mark_completed(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    render_job_id=str(response_payload.get("render_job_id") or render_job_id),
                    output_format="pdf",
                    template_id=str(
                        response_payload.get("template_id") or payload.get("template_id")
                    ),
                    template_version=str(
                        response_payload.get("template_version") or payload.get("template_version")
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
            archived = await self._archive_rendered_job(
                job=rendered,
                snapshot=snapshot,
                render_response=response_payload,
                supersedes_render_job_id=supersedes_render_job_id,
                supersedes_archive_document_id=supersedes_archive_document_id,
                archive_consequence=archive_consequence,
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

    async def _archive_rendered_job(
        self,
        *,
        job: ReportJobLedgerRecord,
        snapshot: Any,
        render_response: dict[str, Any],
        supersedes_render_job_id: str | None = None,
        supersedes_archive_document_id: str | None = None,
        archive_consequence: str | None = None,
    ) -> ReportJobLedgerRecord:
        started_at = perf_counter()
        artifact_base64 = _optional_str(render_response.get("artifact_base64"))
        if artifact_base64 is None:
            # A "rendered" response without artifact bytes is a replay of a render
            # that already completed: lotus-render returns terminal truth without
            # re-rendering, and it does not persist artifact bytes (render#120).
            # This is the timeout-after-successful-render path, and it is
            # recoverable - the retained snapshot regenerates the document
            # deterministically under a fresh render job id via the RFC-0105
            # replay, so the failure must stay retry-eligible and must not blame
            # archive validation for a transport loss.
            failed_job = self._job_ledger.mark_failed(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                failure_category="render_artifact_unrecoverable",
                failure_message=(
                    "The render completed previously but its artifact was only "
                    "available in the original response. Replay the job to "
                    "re-render from the retained snapshot."
                ),
                retry_eligible=True,
            )
            record_report_operation(
                operation="archive_handoff",
                status=failed_job.status,
                failure_category=failed_job.failure_category,
                duration_seconds=perf_counter() - started_at,
            )
            return failed_job

        archive_request_id = job.archive_request_id or f"arch_{job.render_job_id or job.job_id}"
        if job.status == "completed":
            self._job_ledger.mark_archiving(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                archive_request_id=archive_request_id,
            )
        status_code, response_payload = await self._archive_client.archive_document(
            _build_archive_payload(
                job=job,
                snapshot=snapshot,
                render_response=render_response,
                archive_request_id=archive_request_id,
                content_base64=artifact_base64,
                supersedes_render_job_id=supersedes_render_job_id,
                supersedes_archive_document_id=supersedes_archive_document_id,
                archive_consequence=archive_consequence,
            ),
            actor_id=job.triggered_by,
            tenant_id=job.tenant_id,
            region=job.region,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            booking_center_code=job.booking_center_code,
            role=job.role,
        )
        if status_code in {200, 201} and _optional_str(response_payload.get("document_id")):
            archived_job = self._job_ledger.mark_archived(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                archive_request_id=archive_request_id,
                archive_document_id=str(response_payload["document_id"]),
            )
            record_report_operation(
                operation="archive_handoff",
                status=archived_job.status,
                duration_seconds=perf_counter() - started_at,
            )
            return archived_job
        failure_category, retry_eligible = _archive_failure_posture(
            status_code, response_payload, report_type=job.report_type
        )
        failed_job = self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=_archive_failure_message(response_payload),
            retry_eligible=retry_eligible,
        )
        record_report_operation(
            operation="archive_handoff",
            status=failed_job.status,
            failure_category=failed_job.failure_category,
            duration_seconds=perf_counter() - started_at,
        )
        return failed_job


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
        archive_client=ArchiveClient(
            base_url=settings.archive_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        ),
        snapshot_store=get_report_input_snapshot_store(),
        job_ledger=get_report_job_ledger(),
    )


def _build_archive_payload(
    *,
    job: ReportJobLedgerRecord,
    snapshot: Any,
    render_response: dict[str, Any],
    archive_request_id: str,
    content_base64: str,
    render_attempt_id: str | None = None,
    supersedes_render_job_id: str | None = None,
    supersedes_archive_document_id: str | None = None,
    archive_consequence: str | None = None,
) -> dict[str, Any]:
    snapshot_payload = _as_dict(snapshot.snapshot_payload)
    review_period = _as_dict(snapshot_payload.get("reviewPeriod"))
    identity = _as_dict(_as_dict(snapshot_payload.get("clientProfile")).get("identity"))
    portfolio_ids = job.portfolio_scope.get("portfolio_ids")
    portfolio_id = (
        str(portfolio_ids[0])
        if isinstance(portfolio_ids, list) and portfolio_ids
        else "portfolio-not-available"
    )
    reporting_period_start = _date_text(
        review_period.get("start_date")
        or review_period.get("period_start")
        or date(job.as_of_date.year, 1, 1)
    )
    reporting_period_end = _date_text(
        review_period.get("end_date") or review_period.get("period_end") or job.as_of_date
    )
    metadata = {
        "archive_request_id": archive_request_id,
        "report_job_id": job.job_id,
        "report_request_id": job.request_id,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_hash,
        "render_job_id": str(render_response.get("render_job_id") or job.render_job_id),
        "render_attempt_id": str(
            render_attempt_id
            or render_response.get("render_attempt_id")
            or render_response.get("render_job_id")
            or job.render_job_id
            or job.job_id
        ),
        "report_type": job.report_type,
        "portfolio_scope": json.dumps(job.portfolio_scope, sort_keys=True, separators=(",", ":")),
        "portfolio_id": portfolio_id,
        "client_reference": _optional_str(identity.get("client_reference"))
        or _optional_str(identity.get("client_id")),
        "as_of_date": job.as_of_date.isoformat(),
        "reporting_period_start": reporting_period_start,
        "reporting_period_end": reporting_period_end,
        "frequency": _optional_str(review_period.get("frequency")) or "ad_hoc",
        "template_id": str(render_response.get("template_id") or job.render_template_id),
        "template_version": str(
            render_response.get("template_version") or job.render_template_version
        ),
        "render_service_version": _optional_str(render_response.get("runtime_engine_version"))
        or _optional_str(render_response.get("runtime_engine"))
        or "unknown",
        "report_data_contract_version": snapshot.report_data_contract_version,
        "mime_type": "application/pdf",
        "output_format": "pdf",
        "classification": "confidential",
        "region": job.region,
        "tenant_id": job.tenant_id,
        "retention_policy_id": _optional_str(job.options.get("retention_policy_id")),
        "retention_start_date": job.as_of_date.isoformat(),
        "retain_until_date": _optional_str(job.options.get("retain_until_date")),
        "created_by_service": "lotus-report",
        "created_by_actor": job.triggered_by,
    }
    advisor_memo = _advisor_proposal_memo_archive_summary(snapshot_payload)
    if advisor_memo is not None:
        metadata["advisor_proposal_memo"] = advisor_memo
    advisor_commentary = _advisor_commentary_archive_summary(snapshot_payload)
    if advisor_commentary is not None:
        metadata["advisor_commentary"] = advisor_commentary
    if supersedes_render_job_id:
        metadata["supersedes_render_job_id"] = supersedes_render_job_id
    if supersedes_archive_document_id:
        metadata["supersedes_archive_document_id"] = supersedes_archive_document_id
    if archive_consequence:
        metadata["archive_consequence"] = archive_consequence
    return {"metadata": metadata, "content_base64": content_base64}


def _advisor_commentary_archive_summary(
    snapshot_payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Archive metadata keeps the accepted brief's audit identity for a
    rendered ADVISOR_COMMENTARY section (issue #166 acceptance 4)."""

    package = _as_dict(snapshot_payload.get("advisor_commentary_package"))
    if not package or package.get("status") != "included":
        return None
    review = _as_dict(package.get("review"))
    return {
        "run_id": _optional_str(package.get("run_id")) or "not_available",
        "request_id": _optional_str(package.get("request_id")) or "not_available",
        "reviewed_by": _optional_str(review.get("reviewed_by")) or "not_available",
        "reviewed_at": _optional_str(review.get("reviewed_at")) or "not_available",
        "content_hash": _optional_str(package.get("content_hash")) or "not_available",
        "schema_id": _optional_str(package.get("schema_id")) or "not_available",
        "included_in_render": True,
    }


def _advisor_proposal_memo_archive_summary(
    snapshot_payload: dict[str, Any],
) -> dict[str, Any] | None:
    package = _as_dict(snapshot_payload.get("proposal_memo_package"))
    if not package:
        return None
    review = _as_dict(package.get("review"))
    sections = [section for section in package.get("sections", []) if isinstance(section, dict)]
    return {
        "memo_id": _optional_str(package.get("memo_id")) or "not_available",
        "proposal_id": _optional_str(package.get("proposal_id")) or "not_available",
        "proposal_version_no": _optional_int(package.get("proposal_version_no")) or 0,
        "review_event_id": _optional_str(review.get("review_event_id")) or "not_available",
        "review_action": _optional_str(review.get("review_action")) or "not_available",
        "client_ready_status": _optional_str(package.get("client_ready_publication")) or "BLOCKED",
        "memo_hash": _optional_str(package.get("memo_hash")) or "not_available",
        "source_input_hash": _optional_str(package.get("source_input_hash")) or "not_available",
        "section_count": len(sections),
        "blocked_section_count": sum(
            1 for section in sections if section.get("status") == "BLOCKED"
        ),
        "included_in_render": True,
    }


def _date_text(value: object) -> str:
    if isinstance(value, date):
        return str(value.isoformat())
    text = _optional_str(value)
    if text:
        return str(text)
    raise ValueError("date value is required")


def _archive_failure_posture(
    status_code: int, payload: dict[str, Any], *, report_type: str
) -> tuple[str, bool]:
    detail = payload.get("detail")
    detail_payload = detail if isinstance(detail, dict) else {}
    code = str(detail_payload.get("code") or "")
    if status_code in {400, 422} or code in {
        "archive_metadata_invalid",
        "archive_payload_invalid",
    }:
        return "archive_validation_failed", False
    if status_code == 409 or code == "archive_conflict":
        return "archive_conflict", False
    # Retry-eligibility for archive-stage failures is scoped to the one
    # report family whose recovery path can retry SAFELY: portfolio-review
    # replay resolves the original arch_{render_job_id} against archive
    # before re-rendering, so a committed-but-response-lost ingest is adopted
    # rather than duplicated. Other families have no resolution path - a
    # fresh order mints a fresh request id that archive idempotency cannot
    # converge, so advertising retryable there would invite duplicate client
    # documents. (Rerender attempts gained their own resolution-first
    # recovery in issue #215 and override this posture at the attempt level.)
    resolvable = report_type == "portfolio_review"
    if status_code in {503, 507} or code in {
        "archive_storage_unavailable",
        "archive_storage_failed",
    }:
        return "archive_storage_failed", resolvable
    # Unclassified archive faults (including generic 500s) are retryable:
    # archive ingestion is idempotent by the deterministic arch_{render_job_id}
    # request id - an identical retry converges on the existing document after
    # checksum verification - so retrying cannot duplicate a client document
    # or corrupt state, and the usual default-deny argument for unknown faults
    # does not apply to this leg (issue #211).
    return "archive_execution_failed", resolvable


def _archive_failure_message(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return _optional_str(detail.get("message")) or "lotus-archive handoff failed."
    return (
        _optional_str(payload.get("failure_message"))
        or _optional_str(detail)
        or ("lotus-archive handoff failed.")
    )
