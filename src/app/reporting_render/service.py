from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

from app.clients.render_client import RenderClient
from app.config import settings
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_report_input_snapshot_store
from app.reporting_render.package_builder import _build_render_package, _optional_int, _optional_str


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
        if "pdf" not in job.requested_output_formats:
            return job
        if job.status in {"completed", "completed_with_warnings", "failed", "cancelled"}:
            return job
        if job.status == "rendering":
            return job
        if job.status != "data_ready":
            return job

        snapshot = self._snapshot_store.get_snapshot_by_job(job.job_id)
        render_job_id = job.render_job_id or f"rdr_{job.job_id}_pdf"
        payload = _build_render_package(
            job=job,
            snapshot=snapshot.snapshot_payload,
            render_job_id=render_job_id,
        )
        self._job_ledger.mark_rendering(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            render_job_id=render_job_id,
            output_format="pdf",
            template_id="portfolio-review",
            template_version="v1",
        )

        status_code, response_payload = await self._render_client.submit_render_package(
            payload,
            correlation_id=job.correlation_id,
        )
        if status_code in {200, 201} and response_payload.get("status") == "rendered":
            return self._job_ledger.mark_completed(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                render_job_id=str(response_payload.get("render_job_id") or render_job_id),
                output_format="pdf",
                template_id=str(response_payload.get("template_id") or "portfolio-review"),
                template_version=str(response_payload.get("template_version") or "v1"),
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
        return self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=failure_message or "lotus-render execution failed.",
            retry_eligible=retry_eligible,
        )


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
