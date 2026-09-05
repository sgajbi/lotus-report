"""The report#303 recovery corrections, proven on the REAL pipeline.

Real ReportJobWorker + ReportJobExecutionService + SQLite ledger + render
orchestration, with only the Render transport scripted - the two
exact-main reproductions from the 2026-09-05 recovery directive, closed:

1. waiting is not failure: an in-progress render defers beyond the old
   three-poll budget and its eventual completion is ADOPTED under the
   same render id;
2. a completed render recovers across a composer upgrade: resolution runs
   before any package recomposition, while genuinely new submissions keep
   the unsupported-accepted-contract refusal.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.reporting_jobs import ledger as ledger_module
from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    InvalidReportJobWorkTransitionError,
    ReportJobLedger,
)
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
from app.reporting_jobs.worker import ReportJobWorker
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render import service as render_service_module
from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

_RENDERED = {
    "status": "rendered",
    "template_id": "portfolio-review",
    "template_version": "v2",
    "artifact_sha256": "sha256:artifact",
    "bounded_determinism_fingerprint": "fingerprint",
    "runtime_engine": "typst",
    "runtime_engine_version": "0.14.2",
    "render_duration_ms": 812,
    "archive_state": "archived_verified",
    "archive_document_id": "doc_archived",
    "archive_request_id": "areq_recovered",
}


class _ScriptedRenderClient:
    """Render transport scripted per poll; everything else is real."""

    def __init__(self, status_responses, diagnostics=None):
        self._status_responses = list(status_responses)
        self._diagnostics = diagnostics or (
            200,
            {"recovery_action": "wait_for_completion", "retryable": True},
        )
        self.submitted = []
        self.status_calls = 0

    async def get_render_status(self, render_job_id, correlation_id=None, trace_id=None):
        self.status_calls += 1
        if len(self._status_responses) > 1:
            return self._status_responses.pop(0)
        return self._status_responses[0]

    async def get_render_diagnostics(self, render_job_id, correlation_id=None, trace_id=None):
        return self._diagnostics

    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        self.submitted.append(payload)
        return 201, {**_RENDERED, "render_job_id": payload["render_job_id"]}


class _NoCapture:
    async def capture_for_job(self, job):
        raise AssertionError("recovery must not recapture")


def _seed_rendering_job(tmp_path, *, suffix="recovery"):
    ledger = ReportJobLedger(tmp_path / f"jobs-{suffix}.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / f"lineage-{suffix}.sqlite3")
    job = ledger.submit_portfolio_review_job(
        request=PortfolioReviewJobRequest.model_validate(
            {
                "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
                "as_of_date": "2026-04-22",
                "requested_output_formats": ["pdf"],
                "reporting_currency": "USD",
                "options": {"sections": ["OVERVIEW"]},
            }
        ),
        caller_context=ReportCallerContext(
            trigger_type="user",
            triggered_by="advisor-123",
            caller_application="lotus-gateway",
            tenant_id="tenant-sg",
            region="APAC",
            booking_center_code="SG",
            role=None,
            correlation_id="corr-recovery",
            trace_id="trace-recovery",
        ),
        idempotency_key=f"idem-{suffix}",
    )
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload={
                "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
                "readiness": {"status": "ready"},
                "reviewPeriod": {"label": "YTD"},
            },
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-core"], "call_count": 1},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    ledger.mark_collecting_data(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    ledger.mark_data_ready(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    rendering = ledger.mark_rendering(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
        render_job_id=f"rdr_{job.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v2",
    )
    return ledger, store, rendering


def _worker(ledger, store, client):
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=client,
        snapshot_store=store,
        job_ledger=ledger,
    )
    execution = ReportJobExecutionService(
        report_job_ledger=ledger,
        capture_service=_NoCapture(),
        render_service=render_service,
    )
    return ReportJobWorker(
        work_ledger=ledger,
        execution_service=execution,
        retry_policy=ReportJobWorkRetryPolicy(max_attempts=3, base_delay_seconds=1),
    )


def _advance_clock(monkeypatch, seconds):
    later = datetime.now(UTC) + timedelta(seconds=seconds)
    monkeypatch.setattr(ledger_module, "utc_now", lambda: later)


@pytest.mark.asyncio
async def test_waiting_survives_beyond_the_failure_budget_and_adopts(tmp_path, monkeypatch):
    """Reproduction 1, closed: five polls of an in-progress render leave the
    job nonterminal with the failure budget untouched, and the SIXTH poll
    adopts the completed render under the SAME render id - no submission
    ever happens."""

    ledger, store, rendering = _seed_rendering_job(tmp_path)
    in_progress = (200, {"render_job_id": rendering.render_job_id, "status": "rendering"})
    client = _ScriptedRenderClient(
        status_responses=[in_progress] * 5
        + [(200, {**_RENDERED, "render_job_id": rendering.render_job_id})],
    )
    worker = _worker(ledger, store, client)

    for poll in range(5):
        _advance_clock(monkeypatch, seconds=(poll + 1) * 10)
        result = await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)
        assert result.claimed_count == 1, f"poll {poll} claimed nothing"
        assert result.outcomes[0].failure_category == "waiting_on_render"
        assert ledger.get_job(rendering.job_id).status == "rendering"

    _advance_clock(monkeypatch, seconds=100)
    final = await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)

    assert final.completed_count == 1
    adopted = ledger.get_job(rendering.job_id)
    assert adopted.status == "archived"
    assert adopted.render_job_id == rendering.render_job_id
    assert adopted.archive_document_id == "doc_archived"
    assert client.submitted == []


@pytest.mark.asyncio
async def test_a_real_failure_still_exhausts_the_bounded_budget(tmp_path, monkeypatch):
    """The failure budget stays real for real failures: a persistently
    escalated render burns attempts and terminalizes with operator
    intervention required - waiting never does."""

    ledger, store, rendering = _seed_rendering_job(tmp_path, suffix="budget")
    client = _ScriptedRenderClient(
        status_responses=[(200, {"render_job_id": rendering.render_job_id, "status": "rendering"})],
        diagnostics=(
            200,
            {
                "recovery_action": "escalate_render_runtime",
                "retryable": False,
                "stale_state": "stale",
                "support_message": "runtime crashed mid-render",
            },
        ),
    )
    worker = _worker(ledger, store, client)

    _advance_clock(monkeypatch, seconds=10)
    result = await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)

    assert result.completed_count == 1
    failed = ledger.get_job(rendering.job_id)
    assert failed.status == "failed"
    assert failed.failure_category == "render_execution_failed"
    assert failed.retry_eligible is False
    assert "escalate_render_runtime" in (failed.failure_message or "")
    assert client.submitted == []


@pytest.mark.asyncio
async def test_completed_render_recovers_across_a_composer_upgrade(tmp_path, monkeypatch):
    """Reproduction 2, closed: a persisted job whose render already
    completed adopts the owner outcome WITHOUT any package recomposition -
    the composer may be a version the job's accepted contract can no
    longer build."""

    ledger, store, rendering = _seed_rendering_job(tmp_path, suffix="upgrade")

    def _must_not_compose(**kwargs):
        raise AssertionError("recovery of an existing render must not recompose the package")

    monkeypatch.setattr(render_service_module, "_build_render_package", _must_not_compose)
    client = _ScriptedRenderClient(
        status_responses=[(200, {**_RENDERED, "render_job_id": rendering.render_job_id})],
    )
    worker = _worker(ledger, store, client)

    _advance_clock(monkeypatch, seconds=10)
    result = await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)

    assert result.completed_count == 1
    adopted = ledger.get_job(rendering.job_id)
    assert adopted.status == "archived"
    assert adopted.render_job_id == rendering.render_job_id
    assert client.submitted == []


@pytest.mark.asyncio
async def test_concurrent_workers_never_double_execute_a_waiting_item(tmp_path, monkeypatch):
    """Lease fencing holds for deferral exactly as for failure: two workers
    racing one waiting item execute it once."""

    ledger, store, rendering = _seed_rendering_job(tmp_path, suffix="concurrent")
    client = _ScriptedRenderClient(
        status_responses=[(200, {"render_job_id": rendering.render_job_id, "status": "rendering"})],
    )
    worker_a = _worker(ledger, store, client)
    worker_b = _worker(ledger, store, client)

    _advance_clock(monkeypatch, seconds=10)
    result_a, result_b = await asyncio.gather(
        worker_a.run_once(worker_id="wa", max_items=5, lease_seconds=60),
        worker_b.run_once(worker_id="wb", max_items=5, lease_seconds=60),
    )

    assert result_a.claimed_count + result_b.claimed_count == 1
    assert client.status_calls == 1
    assert ledger.get_job(rendering.job_id).status == "rendering"


@pytest.mark.asyncio
async def test_cancellation_stays_bounded_while_waiting(tmp_path, monkeypatch):
    """The cancellation boundary is unchanged by waiting: a job that
    reached rendering refuses cancellation, waiting or not."""

    ledger, _store, rendering = _seed_rendering_job(tmp_path, suffix="cancel")

    with pytest.raises(InvalidReportJobTransitionError):
        ledger.cancel_job(
            job_id=rendering.job_id,
            actor="advisor-123",
            correlation_id="corr-cancel",
            trace_id="trace-cancel",
        )


def test_defer_is_lease_fenced(tmp_path):
    """A stale worker cannot defer an item it no longer owns."""

    ledger, _store, _rendering = _seed_rendering_job(tmp_path, suffix="fence")
    claimed = ledger.claim_work_items(
        worker_id="w1",
        limit=1,
        lease_seconds=60,
        retry_policy=ReportJobWorkRetryPolicy(),
    )
    assert len(claimed) == 1

    with pytest.raises(InvalidReportJobWorkTransitionError):
        ledger.defer_work_item(
            work_item_id=claimed[0].work_item_id,
            lease_token="not-the-lease",
            wait_reason="waiting_on_render",
            delay_seconds=5,
        )

    deferred = ledger.defer_work_item(
        work_item_id=claimed[0].work_item_id,
        lease_token=claimed[0].lease_token,
        wait_reason="waiting_on_render",
        delay_seconds=5,
    )
    assert deferred.status == "retry_pending"
    assert deferred.attempt_count == claimed[0].attempt_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recovery_action", "expected_category", "expected_retryable"),
    [
        ("fix_upstream_render_package", "render_validation_failed", False),
        ("fix_template_registry_or_package", "render_validation_failed", False),
        ("escalate_render_runtime", "render_execution_failed", False),
        ("escalate_template_support", "render_execution_failed", False),
        ("reduce_document_size_or_raise_envelope", "render_validation_failed", False),
        ("escalate_reporting_platform", "render_execution_failed", False),
    ],
)
async def test_the_recovery_mapping_table_row_by_row(
    tmp_path, monkeypatch, recovery_action, expected_category, expected_retryable
):
    """report#303: every owner recovery action maps through the explicit
    table - escalate_template_support stays non-retryable by DELIBERATE
    queue semantics (retry helps only after remediation), never by silent
    shadowing."""

    ledger, store, rendering = _seed_rendering_job(tmp_path, suffix=f"map-{recovery_action[:12]}")
    client = _ScriptedRenderClient(
        status_responses=[(200, {"render_job_id": rendering.render_job_id, "status": "rendering"})],
        diagnostics=(200, {"recovery_action": recovery_action, "retryable": True}),
    )
    worker = _worker(ledger, store, client)

    _advance_clock(monkeypatch, seconds=10)
    await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)

    failed = ledger.get_job(rendering.job_id)
    assert failed.status == "failed"
    assert failed.failure_category == expected_category
    assert failed.retry_eligible is expected_retryable
    assert recovery_action in (failed.failure_message or "")
    assert client.submitted == []


@pytest.mark.asyncio
async def test_an_unmapped_recovery_action_fails_closed_naming_itself(tmp_path, monkeypatch):
    ledger, store, rendering = _seed_rendering_job(tmp_path, suffix="unmapped")
    client = _ScriptedRenderClient(
        status_responses=[(200, {"render_job_id": rendering.render_job_id, "status": "rendering"})],
        diagnostics=(200, {"recovery_action": "brand_new_owner_action", "retryable": True}),
    )
    worker = _worker(ledger, store, client)

    _advance_clock(monkeypatch, seconds=10)
    await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)

    failed = ledger.get_job(rendering.job_id)
    assert failed.status == "failed"
    assert failed.failure_category == "render_execution_failed"
    assert failed.retry_eligible is False
    assert "brand_new_owner_action" in (failed.failure_message or "")


@pytest.mark.asyncio
async def test_an_unanswerable_diagnostics_lookup_waits(tmp_path, monkeypatch):
    """No escalation channel means no escalation: not duplicating a
    document outranks failing fast."""

    ledger, store, rendering = _seed_rendering_job(tmp_path, suffix="diag-down")
    client = _ScriptedRenderClient(
        status_responses=[(200, {"render_job_id": rendering.render_job_id, "status": "rendering"})],
        diagnostics=(503, {"detail": {"code": "unavailable"}}),
    )
    worker = _worker(ledger, store, client)

    _advance_clock(monkeypatch, seconds=10)
    result = await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)

    assert result.outcomes[0].failure_category == "waiting_on_render"
    assert ledger.get_job(rendering.job_id).status == "rendering"


@pytest.mark.asyncio
async def test_a_stale_render_resubmits_convergently_under_the_persisted_id(tmp_path, monkeypatch):
    """The owner's named remedy for a stale in-progress render: an identical
    resubmission under the SAME render id converges by construction
    (create-or-get takeover), dead executor or merely slow - replay's fresh
    render id is never needed."""

    ledger, store, rendering = _seed_rendering_job(tmp_path, suffix="stale-resubmit")
    client = _ScriptedRenderClient(
        status_responses=[(200, {"render_job_id": rendering.render_job_id, "status": "rendering"})],
        diagnostics=(
            200,
            {
                "recovery_action": "resubmit_identical_package_or_escalate_runtime",
                "retryable": True,
                "stale_state": "stale",
            },
        ),
    )
    worker = _worker(ledger, store, client)

    _advance_clock(monkeypatch, seconds=10)
    result = await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)

    assert result.completed_count == 1
    assert len(client.submitted) == 1
    assert client.submitted[0]["render_job_id"] == rendering.render_job_id
    assert ledger.get_job(rendering.job_id).status == "archived"


@pytest.mark.asyncio
async def test_stale_resubmit_never_overrides_ledger_completion_evidence(tmp_path, monkeypatch):
    """A completed job with an owner in-progress anomaly routes to the
    designed loss recovery - completion evidence outranks the anomaly, and
    nothing resubmits."""

    ledger, store, rendering = _seed_rendering_job(tmp_path, suffix="stale-anomaly")
    ledger.mark_completed(
        job_id=rendering.job_id,
        actor=rendering.triggered_by,
        correlation_id=rendering.correlation_id,
        trace_id=rendering.trace_id,
        render_job_id=rendering.render_job_id,
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v2",
        template_publication="development",
        artifact_sha256="sha256:artifact",
        bounded_determinism_fingerprint="fingerprint",
        runtime_engine="typst",
        runtime_engine_version="0.14.2",
        render_duration_ms=812,
    )
    client = _ScriptedRenderClient(
        status_responses=[(200, {"render_job_id": rendering.render_job_id, "status": "rendering"})],
        diagnostics=(
            200,
            {
                "recovery_action": "resubmit_identical_package_or_escalate_runtime",
                "retryable": True,
                "stale_state": "stale",
            },
        ),
    )
    worker = _worker(ledger, store, client)

    _advance_clock(monkeypatch, seconds=10)
    await worker.run_once(worker_id="w1", max_items=5, lease_seconds=60)

    failed = ledger.get_job(rendering.job_id)
    assert failed.status == "failed"
    assert failed.failure_category == "render_artifact_unrecoverable"
    assert client.submitted == []
