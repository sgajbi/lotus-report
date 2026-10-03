"""Capture uses native clients with an in-process transport, never live services."""

import json
from copy import deepcopy

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
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.rerender_service import PortfolioReviewRerenderService
from tests.unit.reporting_render.test_archive_lineage import _LifecycleClient
from tests.unit.reporting_render.test_replay_service import (
    _RecordingRenderClient,
    _recovery_services,
    _RefusingCapture,
)
from tests.unit.services.test_transaction_page_evidence import _TransactionPages
from tests.unit.test_reporting_read_service_additional import _transaction_ledger_metadata


class _RecordingPackageRender(_RecordingRenderClient):
    def __init__(self):
        super().__init__()
        self.packages = []

    async def submit_render_package(self, payload, *args, **kwargs):
        self.packages.append(deepcopy(payload))
        return await super().submit_render_package(payload, *args, **kwargs)


def _pages(case):
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    if case in {"degraded-first", "degraded-last"}:
        page = pages[0 if case == "degraded-first" else 1]
        page.update(
            data_quality_status="PARTIAL",
            reconciliation_status="UNRECONCILED",
            reason_codes=["MISSING_REFERENCE"],
        )
    elif case == "missing-first":
        pages[0].pop("snapshot_id")
    elif case == "revision-drift":
        pages[1]["snapshot_id"] = "different-scope"
    pages[0]["content_hash"] = "sha256:page-one"
    pages[1]["content_hash"] = "sha256:page-two"
    return pages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["degraded-first", "degraded-last", "missing-first", "revision-drift", "healthy"]
)
async def test_native_capture_reopen_and_retained_replay_preserve_page_qualification(
    tmp_path, monkeypatch, case
):
    core = _TransactionPages(_pages(case))
    requests = []

    async def handle(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/reporting/portfolio-summary/query"):
            status, payload = await core.get_portfolio_summary("P1", json.loads(request.content))
        elif request.url.path.endswith("/portfolios/P1/transactions"):
            params = {
                key: int(value) if key in {"skip", "limit"} else value
                for key, value in request.url.params.items()
            }
            status, payload = await core.get_portfolio_transactions(
                "P1", params, admitted_tenant_id="default"
            )
        elif request.url.path.endswith("/portfolios/P1"):
            status, payload = await core.get_portfolio_detail("P1")
        else:
            raise AssertionError(f"Unexpected upstream route: {request.url.path}")
        return httpx.Response(status, json=payload)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: original_client(
            *args, transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    caller = ReportCallerContext(
        triggered_by="advisor",
        caller_application="lotus-gateway",
        tenant_id="default",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id="page-capture",
        trace_id="0123456789abcdef0123456789abcdef",
    )
    ledger_path, store_path = tmp_path / "jobs.sqlite3", tmp_path / "lineage.sqlite3"
    ledger, store = ReportJobLedger(ledger_path), ReportInputSnapshotStore(store_path)
    job = ledger.create_portfolio_review_job(
        request=PortfolioReviewJobRequest(
            portfolio_scope={"portfolio_ids": ["P1"]},
            as_of_date="2026-02-24",
            requested_output_formats=["pdf"],
            reporting_currency="USD",
            options={"sections": ["TRANSACTIONS", "INCOME_AND_ACTIVITY"]},
        ),
        caller_context=caller,
        idempotency_key="capture",
    )
    captured = await PortfolioReviewSnapshotCaptureService(
        snapshot_store=store, job_ledger=ledger
    ).capture_for_job(job)
    assert captured.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    transaction = snapshot.snapshot_payload["transactions"]
    assert transaction["transactionCount"] == 2
    assert transaction["supportability"]["status"] == ("ready" if case == "healthy" else "partial")
    assert len(transaction["sourceProduct"]["page_evidence"]) == 2
    assert len(core.reads) == 2
    assert snapshot.lineage_summary["call_count"] == len(requests)

    ledger.mark_failed(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
        failure_category="render_artifact_unrecoverable",
        failure_message="Controlled retained recovery.",
        retry_eligible=True,
    )
    ledger, store = ReportJobLedger(ledger_path), ReportInputSnapshotStore(store_path)
    reopened = store.get_snapshot_by_job(job.job_id)
    assert reopened.snapshot_payload == snapshot.snapshot_payload
    render = _RecordingPackageRender()
    replay, _, _ = _recovery_services(
        ledger, store, _RefusingCapture(), snapshot_store=store, render_client=render
    )
    result = await replay.replay_job(
        job_id=job.job_id,
        command=ReportJobReplayRequest(reason="Retain page qualification."),
        caller_context=caller,
        idempotency_key="replay",
    )
    assert result.replayed_job.status == "archived"
    cloned = store.get_snapshot_by_job(result.replayed_job.job_id)
    assert cloned.snapshot_payload == reopened.snapshot_payload
    assert store.get_snapshot_by_job(job.job_id).snapshot_hash == snapshot.snapshot_hash
    assert len(core.reads) == 2
    assert len(render.packages) == 1
    expected_notes = [
        {key: note.get(key) for key in ("code", "severity", "message")}
        for note in transaction["supportability"]["notes"]
    ]
    assert render.packages[0]["report_data"]["earnings_statement"]["notes"] == expected_notes
    correction_render = _RecordingPackageRender()
    correction_render._archive_document_id = "doc_correction"
    archive = _LifecycleClient()
    rerender = PortfolioReviewRerenderService(
        render_client=correction_render, archive_client=archive, snapshot_store=store, ledger=ledger
    )
    attempt = await rerender.rerender_job(
        job_id=result.replayed_job.job_id,
        command=ReportJobRerenderRequest(reason="Retain transaction evidence."),
        caller_context=caller,
        idempotency_key="correction",
    )
    assert attempt.status == "archived"
    assert (
        correction_render.packages[0]["report_data"]["earnings_statement"]["notes"]
        == expected_notes
    )
    assert len(core.reads) == 2
    assert (
        store.get_snapshot_by_job(result.replayed_job.job_id).snapshot_payload
        == reopened.snapshot_payload
    )
    assert len(archive.calls) == 1
