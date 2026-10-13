"""Isolated file SQLite proof; synthetic authority is never live grant evidence."""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.reporting_jobs.custody_policy import (
    MAX_OFFER_BYTES,
    CustodyLease,
    CustodyRecoveryError,
    current_work_lease,
    encode_render_offer,
    offer_digest,
)
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_render.retained_offer import RetainedOfferOrchestration


class SyntheticBindings:
    """Test-only explicit positive/negative controls, not a production adapter."""

    def __init__(self, *, denied: bool = False):
        self.denied = denied

    def require_readiness(self):
        if self.denied:
            raise CustodyRecoveryError("synthetic_readiness_denied")

    def admit(self, **scope):
        return True

    def authorize(self, **scope):
        if self.denied:
            raise CustodyRecoveryError("synthetic_authority_denied")

    def transport_headers(self, **scope):
        return {"X-Test-Only": "synthetic"}

    def recovery_action(self, **kwargs):
        return "wait", None

    async def verify_final_custody(self, **kwargs):
        raise CustodyRecoveryError("native_custody_verification_unavailable")


def submit(ledger: ReportJobLedger, key: str = "one", tenant: str = "tenant-sg"):
    return ledger.submit_portfolio_review_job(
        request=PortfolioReviewJobRequest.model_validate(
            {
                "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
                "as_of_date": "2026-04-10",
                "requested_output_formats": ["pdf"],
                "reporting_currency": "USD",
                "options": {"sections": ["OVERVIEW"]},
            }
        ),
        caller_context=ReportCallerContext(
            triggered_by="advisor",
            caller_application="lotus-gateway",
            tenant_id=tenant,
            region="APAC",
            correlation_id="corr-test",
            trace_id="trace-test",
        ),
        idempotency_key=key,
    )


def ready(ledger: ReportJobLedger, key: str = "one", tenant: str = "tenant-sg"):
    job = submit(ledger, key, tenant)
    work = ledger.claim_work_items(worker_id="test", limit=1, lease_seconds=300)[0]
    token = current_work_lease.set((work.work_item_id, work.lease_token or ""))
    scope = {
        "job_id": job.job_id,
        "actor": "test",
        "correlation_id": "corr-test",
        "trace_id": "trace-test",
    }
    try:
        ledger.mark_collecting_data(**scope)
        job = ledger.mark_data_ready(**scope)
    finally:
        current_work_lease.reset(token)
    fence = CustodyLease(work.work_item_id, work.lease_token or "", job.custody_epoch)
    payload = {
        "render_job_id": f"rdr_{job.job_id}_pdf",
        "report_job_id": job.job_id,
        "report_type": job.report_type,
        "output_format": "pdf",
        "template_id": job.render_template_id,
        "template_version": job.render_template_version,
        "render_context": {"archive": {"tenant_id": tenant, "region": "APAC"}},
        "report_data": {"literal": 'é\n\\"'},
    }
    return job, work, fence, payload


def retain(ledger, job, fence, payload):
    return ledger.custody.commit_offer(
        job_id=job.job_id,
        tenant_id=job.tenant_id,
        region=job.region,
        fence=fence,
        payload=payload,
    )


def metadata(ledger, job_id):
    with ledger._connect() as connection:
        return dict(ledger.custody.metadata(connection, job_id))


def test_default_disabled_and_denied_readiness(tmp_path):
    ledger = ReportJobLedger(tmp_path / "legacy.db")
    assert submit(ledger).custody_policy is None
    denied = ReportJobLedger(
        tmp_path / "denied.db", custody_bindings=SyntheticBindings(denied=True)
    )
    with pytest.raises(CustodyRecoveryError, match="readiness"):
        submit(denied)
    with pytest.raises(CustodyRecoveryError, match="authority_unavailable"):
        ledger.custody.load_offer(job_id="unknown", tenant_id="tenant-sg", region="APAC")


def test_exact_bytes_reopen_conflict_scope_and_metadata_projection(tmp_path):
    path = tmp_path / "report.db"
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    digest = retain(ledger, job, fence, payload)
    assert retain(ledger, job, fence, payload) == digest
    reopened = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    body = reopened.custody.load_offer(
        job_id=job.job_id, tenant_id=job.tenant_id, region=job.region
    )
    assert body == encode_render_offer(payload)
    with pytest.raises(CustodyRecoveryError, match="scope"):
        reopened.custody.load_offer(job_id=job.job_id, tenant_id="other", region=job.region)
    with pytest.raises(CustodyRecoveryError, match="conflict"):
        retain(reopened, job, fence, {**payload, "extra": "changed"})
    assert "render_offer_body" not in reopened.get_job(job.job_id).model_dump()
    assert reopened.get_work_item_for_job(job.job_id).work_item_id == work.work_item_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("custody_policy", None),
        ("render_offer_body", b"changed"),
        ("render_job_id", "new-id"),
        ("render_offer_sha256", "0" * 64),
        ("render_offer_generation", 2),
        ("render_template_id", "replacement"),
        ("accepted_document_contract_json", "{}"),
        ("custody_state", "invented"),
        ("custody_state", "verified"),
        ("custody_initial_count", 2),
        ("custody_dispatch_count", 4),
        ("custody_poll_count", 13),
        ("status", "failed"),
        ("status", "cancelled"),
        ("status", "completed"),
        ("status", "completed_with_warnings"),
    ],
)
def test_sql_guards_preserve_original_offer(tmp_path, field, value):
    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, _, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    with pytest.raises(sqlite3.IntegrityError):
        with ledger._connect() as connection:
            connection.execute(
                f"UPDATE report_job SET {field}=? WHERE report_job_id=?", (value, job.job_id)
            )
    assert ledger.custody.load_offer(
        job_id=job.job_id, tenant_id=job.tenant_id, region=job.region
    ) == encode_render_offer(payload)


def test_final_body_bound_and_nonfinite_inputs():
    overhead = len(encode_render_offer({"x": ""}))
    assert len(encode_render_offer({"x": "x" * (MAX_OFFER_BYTES - overhead)})) == MAX_OFFER_BYTES
    with pytest.raises(CustodyRecoveryError, match="size"):
        encode_render_offer({"x": "x" * (MAX_OFFER_BYTES - overhead + 1)})
    with pytest.raises(ValueError):
        encode_render_offer({"x": float("nan")})


def test_three_expired_leases_park_same_work_and_stale_completion_refused(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    instant = datetime.now(UTC)
    first = work
    for attempt in range(3):
        instant += timedelta(seconds=310)
        claimed = ledger.claim_work_items(
            worker_id="restart", limit=1, lease_seconds=300, now=instant
        )
        if attempt < 2:
            assert len(claimed) == 1
        else:
            assert claimed == []
    preserved = ledger.get_job(job.job_id)
    assert preserved.status == "rendering"
    parked = ledger.get_work_item_for_job(job.job_id)
    assert parked.status == "failed" and parked.work_item_id == first.work_item_id
    with pytest.raises(CustodyRecoveryError, match="stale"):
        ledger.complete_work_item(
            work_item_id=first.work_item_id, lease_token=first.lease_token or "", now=instant
        )
    assert not any(
        event.event_type == "job_failed" for event in ledger.list_status_events(job.job_id)
    )


def test_postsend_exhaustion_resume_cas_and_no_body_change(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    digest = retain(ledger, job, fence, payload)
    instant = datetime.now(UTC)
    for attempt in range(3):
        ledger.fail_work_item(
            work_item_id=work.work_item_id,
            lease_token=work.lease_token or "",
            error_category="postsend_exception",
            error_summary="unknown",
            now=instant,
        )
        if attempt < 2:
            instant += timedelta(seconds=20)
            work = ledger.claim_work_items(
                worker_id="retry", limit=1, lease_seconds=300, now=instant
            )[0]
    row = metadata(ledger, job.job_id)
    assert row["status"] == "rendering" and row["custody_state"] == "blocked"
    with pytest.raises(CustodyRecoveryError, match="resume_conflict"):
        ledger.custody.resume(
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            region=job.region,
            expected_epoch=row["custody_epoch"] - 1,
            expected_digest=digest,
        )
    ledger.custody.resume(
        job_id=job.job_id,
        tenant_id=job.tenant_id,
        region=job.region,
        expected_epoch=row["custody_epoch"],
        expected_digest=digest,
    )
    assert ledger.get_work_item_for_job(job.job_id).work_item_id == work.work_item_id
    assert ledger.custody.load_offer(
        job_id=job.job_id, tenant_id=job.tenant_id, region=job.region
    ) == encode_render_offer(payload)


def _hard_exit(
    path: str, job_id: str, fence: CustodyLease, payload: dict[str, Any], committed: bool
):
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    if committed:
        ledger.custody.commit_offer(
            job_id=job_id, tenant_id="tenant-sg", region="APAC", fence=fence, payload=payload
        )
    else:
        connection = sqlite3.connect(path)
        connection.execute("BEGIN IMMEDIATE")
        body = encode_render_offer(payload)
        connection.execute(
            "UPDATE report_job SET render_offer_body=?, render_offer_sha256=?, "
            "render_offer_size=?, render_offer_version='json-utf8-compact.v1', "
            "render_offer_generation=1, render_job_id=?, status='rendering', custody_state='ready' "
            "WHERE report_job_id=?",
            (body, offer_digest(body), len(body), payload["render_job_id"], job_id),
        )
    os._exit(71)  # Actual process death, no exception/connection cleanup.


@pytest.mark.parametrize("committed", [False, True])
def test_hard_process_death_before_and_after_commit(tmp_path, committed):
    path = tmp_path / "report.db"
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    job, _, fence, payload = ready(ledger)
    process = multiprocessing.get_context("spawn").Process(
        target=_hard_exit, args=(str(path), job.job_id, fence, payload, committed)
    )
    process.start()
    process.join(15)
    assert not process.is_alive() and process.exitcode == 71
    reopened = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    row = metadata(reopened, job.job_id)
    assert row["status"] == ("rendering" if committed else "data_ready")
    assert row["custody_reason"] is None
    if committed:
        assert reopened.custody.load_offer(
            job_id=job.job_id, tenant_id=job.tenant_id, region=job.region
        ) == encode_render_offer(payload)


def _quota_writer(path, job_id, tenant_id, fence, payload, cap, barrier, output, quota_scope):
    import app.reporting_jobs.custody_recovery as recovery

    # Scaled quota race; no production cap change.
    if quota_scope == "aggregate":
        recovery.MAX_RETAINED_BYTES = cap
    else:
        recovery.MAX_TENANT_BYTES = cap
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    barrier.wait(timeout=15)
    try:
        ledger.custody.commit_offer(
            job_id=job_id, tenant_id=tenant_id, region="APAC", fence=fence, payload=payload
        )
        output.put("accepted")
    except CustodyRecoveryError as exc:
        output.put(str(exc))


@pytest.mark.parametrize("quota_scope", ["aggregate", "tenant"])
def test_cross_process_scaled_quota_serializes_sum_and_write(tmp_path, quota_scope):
    path = tmp_path / "quota.db"
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    fixtures = [
        ready(
            ledger,
            key=str(i),
            tenant=f"tenant-{i}" if quota_scope == "aggregate" else "tenant-shared",
        )
        for i in range(2)
    ]
    cap = max(len(encode_render_offer(item[3])) for item in fixtures)
    context = multiprocessing.get_context("spawn")
    barrier, output = context.Barrier(2), context.Queue()
    processes = [
        context.Process(
            target=_quota_writer,
            args=(
                str(path),
                job.job_id,
                job.tenant_id,
                fence,
                payload,
                cap,
                barrier,
                output,
                quota_scope,
            ),
        )
        for job, _, fence, payload in fixtures
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert not process.is_alive() and process.exitcode == 0
    assert sorted(output.get(timeout=5) for _ in processes) == [
        "accepted",
        "custody_retention_quota_exceeded",
    ]
    with ledger._connect() as connection:
        total = connection.execute(
            "SELECT SUM(length(render_offer_body)) FROM report_job"
        ).fetchone()[0]
    assert 0 < total <= cap


def _sweep(path, instant, output):
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    claimed = ledger.claim_work_items(
        worker_id=str(os.getpid()), limit=1, lease_seconds=300, now=instant
    )
    output.put([item.lease_token for item in claimed])


def test_concurrent_sweeps_three_expiries_never_terminalize_job(tmp_path):
    path = tmp_path / "sweep.db"
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    job, first, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    context = multiprocessing.get_context("spawn")
    instant = datetime.now(UTC)
    for attempt in range(3):
        instant += timedelta(seconds=310)
        output = context.Queue()
        processes = [
            context.Process(target=_sweep, args=(str(path), instant, output)) for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            assert not process.is_alive() and process.exitcode == 0
        tokens = [token for _ in processes for token in output.get(timeout=5)]
        assert len(tokens) == (1 if attempt < 2 else 0)
    assert ledger.get_job(job.job_id).status == "rendering"
    assert ledger.get_work_item_for_job(job.job_id).work_item_id == first.work_item_id
    assert ledger.get_work_item_for_job(job.job_id).status == "failed"


def test_reservations_not_refunded_and_owner_due_not_pulled_earlier(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, _, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    instant = datetime.now(UTC)
    assert ledger.custody.reserve(**scope, fence=fence, operation="initial", now=instant)
    assert not ledger.custody.reserve(**scope, fence=fence, operation="retry", now=instant)
    assert metadata(ledger, job.job_id)["custody_initial_count"] == 1
    due = instant + timedelta(seconds=200)
    ledger.custody.record_owner_due(**scope, fence=fence, due=due)
    assert not ledger.custody.reserve(
        **scope, fence=fence, operation="retry", now=instant + timedelta(seconds=100)
    )
    assert ledger.custody.reserve(**scope, fence=fence, operation="retry", now=due)
    assert metadata(ledger, job.job_id)["custody_dispatch_count"] == 1


class SyntheticRenderClient:
    def __init__(self, render_id, status=404, payload=None):
        self.render_id, self.status, self.payload = render_id, status, payload or {}
        self.sent = []

    async def read_retained_status(self, **kwargs):
        return self.status, self.payload

    async def submit_retained_offer(self, **kwargs):
        self.sent.append(kwargs["body"])
        return 202, {"render_job_id": self.render_id, "status": "accepted"}

    async def retry_retained_custody(self, **kwargs):
        raise AssertionError("automatic delivery was not admitted by the synthetic binding")


@pytest.mark.asyncio
async def test_known_id_source_off_exact_redelivery_and_named_receipt_retains(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    assert ledger.custody.reserve(**scope, fence=fence, operation="initial")
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE report_job SET custody_next_attempt_at=? WHERE report_job_id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), job.job_id),
        )
    client = SyntheticRenderClient(payload["render_job_id"])
    runtime = RetainedOfferOrchestration(ledger=ledger, client=client)

    def unavailable_source():
        raise AssertionError("known-ID recovery touched source/composer/template path")

    binding = current_work_lease.set((work.work_item_id, work.lease_token or ""))
    try:
        await runtime.advance(ledger.get_job(job.job_id), unavailable_source)
        assert client.sent == [encode_render_offer(payload)]
        with ledger._connect() as connection:
            connection.execute(
                "UPDATE report_job SET custody_next_attempt_at=? WHERE report_job_id=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), job.job_id),
            )
        client.status = 200
        client.payload = {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "archive_state": "archived_verified",
            "archive_request_id": "original",
            "archive_document_id": "named-only",
        }
        await runtime.advance(ledger.get_job(job.job_id), unavailable_source)
    finally:
        current_work_lease.reset(binding)
    assert ledger.get_job(job.job_id).status == "archiving"
    assert ledger.custody.load_offer(**scope) == encode_render_offer(payload)
    assert len(client.sent) == 1


def test_missing_offer_required_cohort_blocks_dispatch_and_preserves_job(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, _, _, _ = ready(ledger)
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE report_job SET status='rendering', render_job_id=? WHERE report_job_id=?",
            (f"rdr_{job.job_id}_pdf", job.job_id),
        )
    with pytest.raises(CustodyRecoveryError, match="integrity"):
        ledger.custody.load_offer(job_id=job.job_id, tenant_id=job.tenant_id, region=job.region)
    ledger.mark_failed(
        job_id=job.job_id,
        actor="test",
        correlation_id="corr-test",
        trace_id="trace-test",
        failure_category="render_execution_failed",
        failure_message="missing offer",
        retry_eligible=True,
    )
    assert ledger.get_job(job.job_id).status == "rendering"
    assert ledger.get_work_item_for_job(job.job_id).status == "failed"


def test_expired_without_reclaim_cannot_reserve_or_finish(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    expired = datetime.now(UTC) + timedelta(seconds=301)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    with pytest.raises(CustodyRecoveryError, match="stale_lease"):
        ledger.custody.reserve(**scope, fence=fence, operation="initial", now=expired)
    with pytest.raises(CustodyRecoveryError, match="stale_lease"):
        ledger.complete_work_item(
            work_item_id=work.work_item_id, lease_token=work.lease_token or "", now=expired
        )
    assert metadata(ledger, job.job_id)["custody_initial_count"] == 0
    assert ledger.get_job(job.job_id).status == "rendering"


@pytest.mark.asyncio
async def test_synthetic_final_verification_preserves_body_and_verified_winner(tmp_path):
    class SyntheticVerifier(SyntheticBindings):
        async def verify_final_custody(self, *, identity):
            assert identity["offer_sha256"] == digest
            assert identity["offer_generation"] == 1

    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticVerifier())
    job, work, fence, payload = ready(ledger)
    digest = retain(ledger, job, fence, payload)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    response = {
        "render_job_id": payload["render_job_id"],
        "archive_request_id": "original",
        "archive_document_id": "document",
        "artifact_sha256": "a" * 64,
    }
    ledger.custody.disposition(**scope, fence=fence, http_status=200, response=response)
    with pytest.raises(sqlite3.IntegrityError):
        with ledger._connect() as connection:
            connection.execute(
                "UPDATE report_job SET archive_request_id='replacement' WHERE report_job_id=?",
                (job.job_id,),
            )
    await ledger.custody.confirm_final(**scope, fence=fence, response=response)
    assert ledger.get_job(job.job_id).status == "archived"
    with pytest.raises(sqlite3.IntegrityError):
        with ledger._connect() as connection:
            connection.execute(
                "UPDATE report_job SET status='failed' WHERE report_job_id=?", (job.job_id,)
            )
    assert ledger.custody.load_offer(**scope) == encode_render_offer(payload)
    ledger.fail_work_item(
        work_item_id=work.work_item_id,
        lease_token=work.lease_token or "",
        error_category="late_failure",
        error_summary="must preserve winner",
    )
    assert ledger.get_job(job.job_id).status == "archived"
    assert ledger.get_work_item_for_job(job.job_id).status == "completed"
    assert [e.event_type for e in ledger.list_status_events(job.job_id)].count("job_archived") == 1


@pytest.mark.asyncio
async def test_worker_returns_parked_outcome_without_rescheduling(tmp_path):
    from app.reporting_jobs.execution import ReportJobExecutionResult
    from app.reporting_jobs.worker import ReportJobWorker

    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    ledger.defer_work_item(
        work_item_id=work.work_item_id,
        lease_token=work.lease_token or "",
        wait_reason="synthetic_wait",
        delay_seconds=1,
        now=datetime.now(UTC) - timedelta(seconds=2),
    )

    class Executor:
        async def execute_job(self, *, job_id):
            current = ledger.get_job(job_id)
            identity = current_work_lease.get()
            assert identity is not None
            ledger.custody.block(
                job_id=job_id,
                tenant_id=current.tenant_id,
                region=current.region,
                fence=CustodyLease(*identity, current.custody_epoch),
            )
            return ReportJobExecutionResult(job=ledger.get_job(job_id), waiting_on_owner=True)

    result = await ReportJobWorker(work_ledger=ledger, execution_service=Executor()).run_once(
        worker_id="park-control", max_items=1, lease_seconds=300
    )
    assert result.failed_count == 1
    assert result.outcomes[0].job_status == "rendering"
    assert ledger.get_work_item_for_job(job.job_id).status == "failed"


@pytest.mark.parametrize("exhausted", ["polls", "window"])
def test_finite_window_and_poll_budget_park_without_mutation(tmp_path, exhausted):
    ledger = ReportJobLedger(tmp_path / "report.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    assert ledger.custody.reserve(**scope, fence=fence, operation="poll")
    with ledger._connect() as connection:
        if exhausted == "polls":
            connection.execute(
                "UPDATE report_job SET custody_poll_count=12 WHERE report_job_id=?", (job.job_id,)
            )
        else:
            connection.execute(
                "UPDATE report_job SET custody_window_started_at=? WHERE report_job_id=?",
                ((datetime.now(UTC) - timedelta(hours=24)).isoformat(), job.job_id),
            )
    assert not ledger.custody.reserve(**scope, fence=fence, operation="poll")
    assert ledger.get_work_item_for_job(job.job_id).status == "failed"
    assert ledger.get_job(job.job_id).status == "rendering"
    assert ledger.custody.load_offer(**scope) == encode_render_offer(payload)
    assert metadata(ledger, job.job_id)["custody_dispatch_count"] == 0


def _resume(path, job_id, epoch, digest, barrier, output):
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    barrier.wait(timeout=15)
    ledger.custody.resume(
        job_id=job_id,
        tenant_id="tenant-sg",
        region="APAC",
        expected_epoch=epoch,
        expected_digest=digest,
    )
    output.put("resumed")


def test_concurrent_identical_resume_advances_one_epoch(tmp_path):
    path = tmp_path / "resume.db"
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    digest = retain(ledger, job, fence, payload)
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE report_job SET custody_poll_count=12 WHERE report_job_id=?", (job.job_id,)
        )
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    assert not ledger.custody.reserve(**scope, fence=fence, operation="poll")
    epoch = metadata(ledger, job.job_id)["custody_epoch"]
    context = multiprocessing.get_context("spawn")
    barrier, output = context.Barrier(2), context.Queue()
    processes = [
        context.Process(
            target=_resume, args=(str(path), job.job_id, epoch, digest, barrier, output)
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert not process.is_alive() and process.exitcode == 0
    assert [output.get(timeout=5) for _ in processes] == ["resumed", "resumed"]
    assert metadata(ledger, job.job_id)["custody_epoch"] == epoch + 1
    assert ledger.get_work_item_for_job(job.job_id).work_item_id == work.work_item_id
    assert ledger.get_work_item_for_job(job.job_id).status == "retry_pending"
    assert ledger.custody.load_offer(**scope) == encode_render_offer(payload)


@pytest.mark.parametrize("expired", [False, True])
def test_synthetic_database_clock_controls_post_lock_lease(expired):
    from app.reporting_jobs.custody_recovery import CustodyRecovery

    local = datetime.now(UTC)
    database = local + timedelta(seconds=301 if expired else 1)

    class Connection:
        def execute(self, query):
            return self

        def fetchone(self):
            return {"instant": database}

    custody = CustodyRecovery(None, postgres=True, bindings=None)
    work = {
        "work_item_id": "work",
        "status": "leased",
        "lease_token": "token",
        "lease_expires_at": local + timedelta(seconds=300),
    }
    fence = CustodyLease("work", "token", 1)
    if expired:
        with pytest.raises(CustodyRecoveryError, match="stale_lease"):
            custody.lease(Connection(), work, {"custody_epoch": 1}, fence, local)
    else:
        custody.lease(Connection(), work, {"custody_epoch": 1}, fence, local)


@pytest.mark.parametrize("jitter", [0, 10])
def test_correction_each_recovery_gap_and_jitter_endpoint(tmp_path, monkeypatch, jitter):
    import app.reporting_jobs.custody_recovery as recovery

    def bounded_jitter(stop):
        if jitter == 10:
            assert stop == 11
        return jitter

    monkeypatch.setattr(recovery, "randbelow", bounded_jitter)
    ledger = ReportJobLedger(tmp_path / "schedule.db", custody_bindings=SyntheticBindings())
    job, _, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    instant = datetime.now(UTC)
    # Isolate scheduling from reclaim: a finite synthetic one-hour lease.
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE report_job_work_item SET lease_expires_at=? WHERE work_item_id=?",
            ((instant + timedelta(hours=1)).isoformat(), fence.work_item_id),
        )
    for index, gap in enumerate((60, 300, 900)):
        operation = "initial" if index == 0 else "retry"
        assert ledger.custody.reserve(**scope, fence=fence, operation=operation, now=instant)
        row = metadata(ledger, job.job_id)
        due = datetime.fromisoformat(row["custody_next_attempt_at"])
        assert (due - instant).total_seconds() == gap + jitter
        assert row["custody_dispatch_count"] == index
        assert not ledger.custody.reserve(
            **scope, fence=fence, operation="retry", now=due - timedelta(microseconds=1)
        )
        instant = due
    assert ledger.custody.reserve(**scope, fence=fence, operation="retry", now=instant)
    assert metadata(ledger, job.job_id)["custody_dispatch_count"] == 3


@pytest.mark.asyncio
async def test_correction_owner_wait_due_survives_restart_without_poll(tmp_path):
    class OwnerWait(SyntheticBindings):
        def recovery_action(self, **kwargs):
            return "wait", due

    path = tmp_path / "owner-wait.db"
    ledger = ReportJobLedger(path, custody_bindings=OwnerWait())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    assert ledger.custody.reserve(**scope, fence=fence, operation="initial")
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE report_job SET custody_next_attempt_at=? WHERE report_job_id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), job.job_id),
        )
    due = datetime.now(UTC) + timedelta(hours=1)
    client = SyntheticRenderClient(payload["render_job_id"])
    client.status = 200
    client.payload = {
        "render_job_id": payload["render_job_id"],
        "status": "rendered",
        "archive_request_id": "original",
    }
    token = current_work_lease.set((work.work_item_id, work.lease_token or ""))
    try:
        await RetainedOfferOrchestration(ledger=ledger, client=client).advance(
            ledger.get_job(job.job_id), lambda: pytest.fail("source reacquired")
        )
        assert (
            datetime.fromisoformat(metadata(ledger, job.job_id)["custody_next_attempt_at"]) == due
        )
        polls = metadata(ledger, job.job_id)["custody_poll_count"]
        reopened = ReportJobLedger(path, custody_bindings=OwnerWait())

        class NoEarlyPoll(SyntheticRenderClient):
            async def read_retained_status(self, **kwargs):
                pytest.fail("polled before owner due")

        await RetainedOfferOrchestration(
            ledger=reopened, client=NoEarlyPoll(payload["render_job_id"])
        ).advance(reopened.get_job(job.job_id), lambda: pytest.fail("source reacquired"))
        assert metadata(reopened, job.job_id)["custody_poll_count"] == polls
        reopened.defer_work_item(
            work_item_id=work.work_item_id,
            lease_token=work.lease_token or "",
            wait_reason="waiting_on_render",
            delay_seconds=5,
        )
        assert reopened.get_work_item_for_job(job.job_id).available_at == due
        assert (
            reopened.claim_work_items(
                worker_id="early", limit=1, lease_seconds=300, now=due - timedelta(seconds=1)
            )
            == []
        )
        resumed = reopened.claim_work_items(worker_id="due", limit=1, lease_seconds=300, now=due)[0]
        resumed_job = reopened.get_job(job.job_id)
        assert resumed.work_item_id == work.work_item_id
        assert reopened.custody.reserve(
            **scope,
            fence=CustodyLease(
                resumed.work_item_id, resumed.lease_token or "", resumed_job.custody_epoch
            ),
            operation="poll",
            now=due,
        )
    finally:
        current_work_lease.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("settlement", ["complete", "defer", "fail"])
async def test_correction_worker_budget_covers_claim_and_settlement(monkeypatch, settlement):
    import app.reporting_jobs.worker as worker
    from tests.unit.reporting_jobs.test_report_job_worker import _Executor, _Ledger

    active = False
    original = worker.asyncio.timeout

    class Budget:
        def __init__(self, seconds):
            self.timeout = original(seconds)

        async def __aenter__(self):
            nonlocal active
            active = True
            return await self.timeout.__aenter__()

        async def __aexit__(self, *args):
            nonlocal active
            try:
                return await self.timeout.__aexit__(*args)
            finally:
                active = False

    class Ledger(_Ledger):
        def claim_work_items(self, **kwargs):
            assert active, "claim/sweeper lies outside item budget"
            return super().claim_work_items(**kwargs)

        def complete_work_item(self, **kwargs):
            assert active, "settlement lies outside item budget"
            return super().complete_work_item(**kwargs)

        def defer_work_item(self, **kwargs):
            assert active, "deferral lies outside item budget"
            return super().defer_work_item(**kwargs)

        def fail_work_item(self, **kwargs):
            assert active, "failure settlement lies outside item budget"
            return super().fail_work_item(**kwargs)

    monkeypatch.setattr(worker.asyncio, "timeout", Budget)
    executor = _Executor(
        waiting=settlement == "defer",
        error=RuntimeError("synthetic failure") if settlement == "fail" else None,
    )
    result = await worker.ReportJobWorker(
        work_ledger=Ledger(), execution_service=executor
    ).run_once(worker_id="worker-1", max_items=1, lease_seconds=60)
    assert result.claimed_count == 1
    assert result.completed_count + result.retry_pending_count == 1


@pytest.mark.parametrize("refusal", ["naive", "stale", "authority"])
def test_correction_owner_due_refusal_makes_no_write(tmp_path, refusal):
    ledger = ReportJobLedger(tmp_path / "refusal.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    before = metadata(ledger, job.job_id)
    due = datetime.now(UTC) + timedelta(hours=1)
    if refusal == "naive":
        due = due.replace(tzinfo=None)
    elif refusal == "stale":
        fence = CustodyLease(work.work_item_id, "stale", fence.epoch)
    else:

        class DenyCurrentAction(SyntheticBindings):
            def authorize(self, **scope):
                raise CustodyRecoveryError("synthetic_current_action_denied")

        ledger.custody.bindings = DenyCurrentAction()
    with pytest.raises(CustodyRecoveryError):
        ledger.custody.record_owner_due(
            job_id=job.job_id, tenant_id=job.tenant_id, region=job.region, fence=fence, due=due
        )
    assert metadata(ledger, job.job_id) == before
    assert ledger.get_work_item_for_job(job.job_id).lease_token == work.lease_token


def test_correction_owner_due_outside_window_parks_without_implicit_resume(tmp_path):
    ledger = ReportJobLedger(tmp_path / "exhausted-due.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    digest = retain(ledger, job, fence, payload)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    assert not ledger.custody.record_owner_due(
        **scope, fence=fence, due=datetime.now(UTC) + timedelta(hours=25)
    )
    row = metadata(ledger, job.job_id)
    assert row["custody_state"] == "blocked" and row["status"] == "rendering"
    assert ledger.get_work_item_for_job(job.job_id).status == "failed"
    with pytest.raises(CustodyRecoveryError, match="resume_conflict"):
        ledger.custody.resume(**scope, expected_epoch=row["custody_epoch"], expected_digest=digest)
    assert ledger.custody.load_offer(**scope) == encode_render_offer(payload)
    assert ledger.get_work_item_for_job(job.job_id).work_item_id == work.work_item_id


@pytest.mark.asyncio
async def test_correction_slow_claim_uses_execution_allowance_and_stops_pass(monkeypatch):
    import asyncio
    import time

    import app.reporting_jobs.worker as worker
    from app.reporting_jobs.execution import ReportJobExecutionResult
    from tests.unit.reporting_jobs.test_report_job_worker import _job, _Ledger, _work_item

    monkeypatch.setattr(worker, "WORK_ITEM_SECONDS", 0.2)
    monkeypatch.setattr(worker, "WORK_PASS_SECONDS", 0.4)
    cancelled = False

    class Ledger(_Ledger):
        def claim_work_items(self, **kwargs):
            if self.claimed:
                time.sleep(0.08)  # Synchronous test double, included in the allowance.
            return super().claim_work_items(**kwargs)

    class Executor:
        calls = 0

        async def execute_job(self, **kwargs):
            nonlocal cancelled
            self.calls += 1
            try:
                await asyncio.sleep(0.17 if self.calls == 1 else 0.2)
            except asyncio.CancelledError:
                cancelled = True
                raise
            return ReportJobExecutionResult(job=_job(), waiting_on_owner=False)

    ledger = Ledger(items=[_work_item(), _work_item(work_item_id="rwork_2")])
    result = await worker.ReportJobWorker(
        work_ledger=ledger, execution_service=Executor()
    ).run_once(worker_id="worker-1", max_items=2, lease_seconds=60)
    assert cancelled
    assert result.claimed_count == 2 and result.completed_count == 1
    assert result.outcomes[1].failure_category == "report_job_worker_deadline_expired"
    assert ledger.failed == []  # No new DB settlement after the allowance expires.


def _hold_sqlite_writer(path, ready_signal, trigger_signal=None, started_signal=None):
    import time

    if trigger_signal is not None:
        started_signal.set()
        assert trigger_signal.wait(15)
    connection = sqlite3.connect(path)
    connection.execute("BEGIN IMMEDIATE")
    ready_signal.set()
    time.sleep(0.3)
    connection.rollback()
    connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["claim", "settlement"])
async def test_native_sqlite_lock_wait_exposes_absolute_deadline_prerequisite(
    tmp_path, monkeypatch, record_property, phase
):
    import time

    import app.reporting_jobs.worker as worker

    path = tmp_path / "blocking-claim.db"
    ledger = ReportJobLedger(path, custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    ledger.defer_work_item(
        work_item_id=work.work_item_id,
        lease_token=work.lease_token or "",
        wait_reason="synthetic_wait",
        delay_seconds=1,
        now=datetime.now(UTC) - timedelta(seconds=2),
    )
    context = multiprocessing.get_context("spawn")
    ready_signal = context.Event()
    trigger = context.Event() if phase == "settlement" else None
    started_signal = context.Event()
    process = context.Process(
        target=_hold_sqlite_writer, args=(str(path), ready_signal, trigger, started_signal)
    )
    process.start()
    assert (started_signal if trigger is not None else ready_signal).wait(15)
    monkeypatch.setattr(worker, "WORK_ITEM_SECONDS", 0.05)
    monkeypatch.setattr(worker, "WORK_PASS_SECONDS", 0.1)

    class NoExecutionAfterClaimExpiry:
        async def execute_job(self, **kwargs):
            if trigger is None:
                pytest.fail("execution started after blocking claim used the allowance")
            from app.reporting_jobs.execution import ReportJobExecutionResult

            trigger.set()
            assert ready_signal.wait(15)
            return ReportJobExecutionResult(job=ledger.get_job(job.job_id), waiting_on_owner=True)

    claims = 0
    native_claim = ledger.claim_work_items

    def counted_claim(**kwargs):
        nonlocal claims
        claims += 1
        return native_claim(**kwargs)

    monkeypatch.setattr(ledger, "claim_work_items", counted_claim)

    started = time.monotonic()
    try:
        result = await worker.ReportJobWorker(
            work_ledger=ledger, execution_service=NoExecutionAfterClaimExpiry()
        ).run_once(worker_id="blocking-proof", max_items=2, lease_seconds=300)
        elapsed = time.monotonic() - started
    finally:
        process.join(15)
    assert process.exitcode == 0 and not process.is_alive()
    # Native dependency evidence, NOT a hard-bound pass: async cancellation did
    # not interrupt SQLite's writer wait. Future activation must bind DB deadlines.
    assert elapsed > 0.1
    record_property("native_sqlite_phase", phase)
    record_property("native_sqlite_elapsed_seconds", elapsed)
    record_property("scaled_item_target_seconds", 0.05)
    record_property("scaled_pass_target_seconds", 0.1)
    record_property("deadline_prerequisite", "blocking DB call was not interrupted")
    assert claims == 1
    expected_outcome = "unknown" if phase == "claim" else "retry_pending"
    expected_row = "leased" if phase == "claim" else "retry_pending"
    assert result.outcomes[0].work_status == expected_outcome
    assert ledger.get_job(job.job_id).status == "rendering"
    assert ledger.get_work_item_for_job(job.job_id).status == expected_row
    assert ledger.custody.load_offer(
        job_id=job.job_id, tenant_id=job.tenant_id, region=job.region
    ) == encode_render_offer(payload)
