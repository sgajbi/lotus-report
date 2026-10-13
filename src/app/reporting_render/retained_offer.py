"""Required-cohort orchestration, exclusively on the original retained Render offer."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Protocol

from app.clients.render_client import RenderClient
from app.reporting_jobs.custody_policy import (
    CustodyLease,
    CustodyRecoveryError,
    CustodyScope,
    current_work_lease,
)
from app.reporting_jobs.custody_recovery import CustodyRecovery
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_render.waiting import RenderWaiting


class CustodyJobLedger(Protocol):
    custody: CustodyRecovery

    def get_job(self, job_id: str) -> ReportJobLedgerRecord: ...


class RetainedOfferOrchestration:
    def __init__(self, *, ledger: CustodyJobLedger, client: RenderClient):
        self.ledger = ledger
        self.custody: CustodyRecovery = ledger.custody
        self.client = client

    async def advance(
        self, job: ReportJobLedgerRecord, initial_package: Callable[[], dict[str, Any]]
    ) -> RenderWaiting | ReportJobLedgerRecord:
        identity = current_work_lease.get()
        if identity is None:
            raise CustodyRecoveryError("custody_worker_lease_required")
        fence = CustodyLease(identity[0], identity[1], job.custody_epoch)
        scope: CustodyScope = {
            "job_id": job.job_id,
            "tenant_id": job.tenant_id,
            "region": job.region,
        }
        self.custody.authorize("advance", **scope)
        async with asyncio.timeout(60):
            if job.status == "data_ready":
                self.custody.commit_offer(**scope, fence=fence, payload=initial_package())
            # First send and every recovery load committed bytes. A failed/ambiguous
            # commit never falls through to a send from the caller's memory.
            body = self.custody.load_offer(**scope)
            row = self.custody.observe(job.job_id)
            render_id = str(row["render_job_id"])
            if row["custody_initial_count"] == 0:
                outcome = await self._submit(scope, fence, body, "initial")
            else:
                if not self.custody.reserve(**scope, fence=fence, operation="poll"):
                    return RenderWaiting(job=self.ledger.get_job(job.job_id))
                status, payload = await self.client.read_retained_status(
                    render_job_id=render_id,
                    authorized_headers=self.custody.headers(operation="poll", **scope),
                )
                if status == 404 and job.status == "rendering":
                    outcome = await self._submit(scope, fence, body, "retry")
                elif status in {200, 201} and payload.get("status") == "rendered":
                    if payload.get("render_job_id") != render_id:
                        raise CustodyRecoveryError("custody_identity_conflict")
                    bindings = self.custody.bindings
                    if bindings is None:
                        raise CustodyRecoveryError("custody_authority_unavailable")
                    action, due = bindings.recovery_action(response=payload)
                    if action not in {"wait", "retry", "verify", "blocked"}:
                        raise CustodyRecoveryError("custody_owner_action_invalid")
                    if action in {"wait", "retry"} and due is not None:
                        if not self.custody.record_owner_due(**scope, fence=fence, due=due):
                            return RenderWaiting(job=self.ledger.get_job(job.job_id))
                    if action != "retry":
                        self.custody.disposition(
                            **scope, fence=fence, http_status=status, response=payload
                        )
                        if action == "blocked":
                            self.custody.block(**scope, fence=fence)
                            return RenderWaiting(job=self.ledger.get_job(job.job_id))
                        if action == "verify":
                            await self.custody.confirm_final(**scope, fence=fence, response=payload)
                            return self.ledger.get_job(job.job_id)
                        return RenderWaiting(job=self.ledger.get_job(job.job_id))
                    if not self.custody.reserve(**scope, fence=fence, operation="retry"):
                        return RenderWaiting(job=self.ledger.get_job(job.job_id))
                    outcome = await self.client.retry_retained_custody(
                        render_job_id=render_id,
                        authorized_headers=self.custody.headers(operation="retry", **scope),
                    )
                else:
                    outcome = status, payload
            if outcome is not None:
                self.custody.disposition(
                    **scope, fence=fence, http_status=outcome[0], response=outcome[1]
                )
            return RenderWaiting(job=self.ledger.get_job(job.job_id))

    async def _submit(
        self, scope: CustodyScope, fence: CustodyLease, body: bytes, operation: str
    ) -> tuple[int, dict[str, Any]] | None:
        if not self.custody.reserve(**scope, fence=fence, operation=operation):
            return None
        return await self.client.submit_retained_offer(
            body=body, authorized_headers=self.custody.headers(operation=operation, **scope)
        )
