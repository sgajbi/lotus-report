from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_render.waiting import RenderWaiting


class ReportJobExecutionLedger(Protocol):
    def get_job(self, job_id: str) -> ReportJobLedgerRecord: ...


class ReportSnapshotCaptureService(Protocol):
    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord: ...


class ReportRenderOrchestrationService(Protocol):
    async def render_for_job(
        self, job: ReportJobLedgerRecord
    ) -> ReportJobLedgerRecord | RenderWaiting: ...


@dataclass(frozen=True)
class ReportJobExecutionResult:
    """One execution pass: the job, plus the explicit waiting posture."""

    job: ReportJobLedgerRecord
    waiting_on_owner: bool


class ReportJobExecutionService:
    """Advance one durable report job through its source-owned processing stages."""

    def __init__(
        self,
        *,
        report_job_ledger: ReportJobExecutionLedger,
        capture_service: ReportSnapshotCaptureService,
        render_service: ReportRenderOrchestrationService,
    ) -> None:
        self._report_job_ledger = report_job_ledger
        self._capture_service = capture_service
        self._render_service = render_service

    async def execute_job(self, *, job_id: str) -> ReportJobExecutionResult:
        job = self._report_job_ledger.get_job(job_id)
        if job.status in {"accepted", "collecting_data"}:
            job = await self._capture_service.capture_for_job(job)
        if job.status in {"data_ready", "rendering", "completed", "archiving"} and (
            "pdf" in job.requested_output_formats
        ):
            rendered = await self._render_service.render_for_job(job)
            if isinstance(rendered, RenderWaiting):
                # The EXPLICIT waiting outcome: whatever recovery status the
                # job holds, the caller defers - a completed job with an
                # unresolved custody outcome must keep polling, never be
                # terminally completed on a transient owner outage.
                return ReportJobExecutionResult(job=rendered.job, waiting_on_owner=True)
            job = rendered
        return ReportJobExecutionResult(job=job, waiting_on_owner=False)
