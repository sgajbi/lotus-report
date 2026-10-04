"""Native Risk HTTP client and SQLite retention; not OS/PG or live-supplier proof."""

import json
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest

from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobReplayRequest,
    ReportJobRerenderRequest,
)
from app.reporting_lineage.capture_service import PortfolioReviewSnapshotCaptureService
from app.reporting_lineage.store import ReportInputSnapshotStore, canonical_json_dumps
from app.reporting_render.rerender_service import PortfolioReviewRerenderService
from tests.unit.reporting_render.test_archive_lineage import _LifecycleClient
from tests.unit.reporting_render.test_replay_service import _recovery_services, _RefusingCapture
from tests.unit.services.test_risk_source_qualification import risk_wire
from tests.unit.services.test_transaction_page_retention import _RecordingPackageRender
from tests.unit.test_reporting_read_service import (
    _CoreQueryClientSuccess,
    _PerformanceClientSuccess,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,reason",
    [
        ("ready", "calculation_complete"),
        ("stale", "stale_source_observations"),
        ("degraded", "calculation_quality_issue"),
    ],
)
@pytest.mark.parametrize("limited_route", ["calculate", "rolling-metrics"])
async def test_capture_reopen_replay_and_rerender_retain_risk_qualification(
    tmp_path, monkeypatch, state, reason, limited_route, adapters=None, verify_retained=None
):
    paths, wires = [], []
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.CoreQueryClient",
        lambda **kw: _CoreQueryClientSuccess(),
    )
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient",
        lambda **kw: _PerformanceClientSuccess(),
    )

    async def handle(request):
        paths.append(request.url.path)
        payload = json.loads(request.content)
        if request.url.path.endswith(("/calculate", "/rolling-metrics")):
            limited = request.url.path.endswith("/" + limited_route)
            response = await risk_wire(
                payload,
                request.url.path,
                state if limited else "ready",
                reason if limited else "calculation_complete",
            )
            wires.append(deepcopy(response))
            return httpx.Response(200, json=response)
        raise AssertionError(f"Unexpected route {request.url.path}")

    native_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: native_client(*a, transport=httpx.MockTransport(handle), **kw),
    )
    caller = ReportCallerContext(
        triggered_by="advisor",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id="risk-retention",
        trace_id="0123456789abcdef0123456789abcdef",
    )
    ledger_path, store_path = tmp_path / "jobs.sqlite3", tmp_path / "lineage.sqlite3"

    def open_adapters():
        return (
            adapters()
            if adapters
            else (ReportJobLedger(ledger_path), ReportInputSnapshotStore(store_path))
        )

    ledger, store = open_adapters()
    job = ledger.create_portfolio_review_job(
        request=PortfolioReviewJobRequest(
            portfolio_scope={"portfolio_ids": ["P1"]},
            as_of_date="2026-02-24",
            requested_output_formats=["pdf"],
            reporting_currency="USD",
            options={"sections": ["RISK_ANALYTICS"]},
        ),
        caller_context=caller,
        idempotency_key="risk-capture-" + uuid4().hex,
    )
    captured = await PortfolioReviewSnapshotCaptureService(
        snapshot_store=store, job_ledger=ledger
    ).capture_for_job(job)
    assert captured.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    risk_calls = [
        call
        for call in store.list_upstream_calls(snapshot.snapshot_id)
        if call.service_name == "lotus-risk"
    ]
    assert len(risk_calls) == len(wires) == 2
    wire_by_route = dict(zip(paths, wires, strict=True))
    for call in risk_calls:
        assert (
            call.response_hash
            == "sha256:"
            + sha256(canonical_json_dumps(wire_by_route[call.endpoint]).encode()).hexdigest()
        )
    assert snapshot.snapshot_payload["readiness"]["status"] == (
        "ready" if state == "ready" else "partial"
    )
    assert snapshot.snapshot_payload["riskAnalytics"]["summary"]["YTD"]["volatility"] == 0.12
    assert (
        snapshot.snapshot_payload["riskTrend"]["results"]
        == wire_by_route["/analytics/risk/rolling-metrics"]["results"]
    )
    ledger.mark_failed(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
        failure_category="render_artifact_unrecoverable",
        failure_message="Controlled retained recovery.",
        retry_eligible=True,
    )
    if adapters:
        ledger.close()
        store.close()
    if verify_retained:
        verify_retained(job.job_id, snapshot)
    ledger, store = open_adapters()
    reopened = store.get_snapshot_by_job(job.job_id)
    assert reopened.snapshot_payload == snapshot.snapshot_payload
    render = _RecordingPackageRender()
    replay, _, _ = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store, render_client=render
    )
    result = await replay.replay_job(
        job_id=job.job_id,
        command=ReportJobReplayRequest(reason="Retain Risk qualification."),
        caller_context=caller,
        idempotency_key="risk-replay",
    )
    assert result.replayed_job.status == "archived"
    retained = store.get_snapshot_by_job(result.replayed_job.job_id)
    assert retained.snapshot_payload == reopened.snapshot_payload
    assert retained.report_revision_id == reopened.report_revision_id is not None
    projected = render.packages[0]["report_data"]["risk_posture"]
    if state != "ready":
        assert reason in {note["code"] for note in projected["notes"]}
    correction = _RecordingPackageRender()
    correction._archive_document_id = "doc_risk_correction"
    rerender = PortfolioReviewRerenderService(
        render_client=correction,
        archive_client=_LifecycleClient(),
        snapshot_store=store,
        ledger=ledger,
    )
    attempt = await rerender.rerender_job(
        job_id=result.replayed_job.job_id,
        command=ReportJobRerenderRequest(reason="Retain Risk limitation."),
        caller_context=caller,
        idempotency_key="risk-correction",
    )
    assert attempt.status == "archived"
    assert correction.packages[0]["report_data"]["risk_posture"] == projected
    assert len(paths) == 2
