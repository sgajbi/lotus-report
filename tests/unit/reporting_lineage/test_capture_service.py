from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException

from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_lineage import service as lineage_service
from app.reporting_lineage.capture_service import (
    PortfolioReviewSnapshotCaptureService,
    _classify_call,
    _first_portfolio_id,
    _hash_payload,
    _lineage_summary,
    _map_job_failure,
    _overall_posture,
    _payload_contract_version,
    _RecordingCoreQueryClient,
    _RecordingPerformanceClient,
    _RecordingRiskClient,
    _request_payload,
    _UpstreamRecorder,
)
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.store import ReportInputSnapshotStore


def _request(**overrides):
    payload = {
        "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "as_of_date": "2026-04-22",
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
    }
    payload.update(overrides)
    return PortfolioReviewJobRequest.model_validate(payload)


def _caller(**overrides):
    payload = {
        "triggered_by": "advisor-123",
        "caller_application": "lotus-gateway",
        "tenant_id": "tenant-sg",
        "region": "APAC",
        "booking_center_code": "SG",
        "role": "advisor",
        "correlation_id": "corr-101",
        "trace_id": "trace-101",
    }
    payload.update(overrides)
    return ReportCallerContext.model_validate(payload)


def _create_job(tmp_path, *, suffix: str = "capture"):
    ledger = ReportJobLedger(tmp_path / f"jobs-{suffix}.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / f"lineage-{suffix}.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key=f"idem-{suffix}",
    )
    return ledger, store, job


class _DummyCoreClient:
    def __init__(self, **_kwargs):
        pass

    async def get_portfolio_summary(self, portfolio_id, payload, correlation_id=None):
        return 200, {
            "contract_version": "v1",
            "portfolio_id": portfolio_id,
            "summary": payload,
            "correlation_id": correlation_id,
        }

    async def get_asset_allocation(self, portfolio_id, payload, correlation_id=None):
        return 200, {
            "contract_version": "v1",
            "portfolio_id": portfolio_id,
            "allocation": payload,
            "correlation_id": correlation_id,
        }

    async def get_portfolio_transactions(self, portfolio_id, params, correlation_id=None):
        return 200, {
            "contract_version": "v1",
            "portfolio_id": portfolio_id,
            "params": params,
            "correlation_id": correlation_id,
        }

    async def get_portfolio_positions(self, portfolio_id, params, correlation_id=None):
        return 200, {
            "contract_version": "v1",
            "portfolio_id": portfolio_id,
            "params": params,
            "correlation_id": correlation_id,
        }

    async def get_portfolio_detail(self, portfolio_id, correlation_id=None):
        return 200, {
            "contract_version": "v1",
            "portfolio_id": portfolio_id,
            "correlation_id": correlation_id,
        }


class _DummyPerformanceClient:
    def __init__(self, **_kwargs):
        pass

    async def get_workspace_summary(self, payload):
        return 200, {"contract_version": "v1", "workspace": payload}

    async def get_contribution(self, payload):
        return 200, {"contract_version": "v1", "contribution": payload}


class _DummyRiskClient:
    def __init__(self, **_kwargs):
        pass

    async def calculate_risk(self, payload):
        return 200, {"contract_version": "v1", "risk": payload}


class _FailingPerformanceClient(_DummyPerformanceClient):
    async def get_workspace_summary(self, payload):
        raise RuntimeError(f"workspace failure for {payload['portfolio_id']}")

    async def get_contribution(self, payload):
        raise RuntimeError(f"contribution failure for {payload['portfolio_id']}")


class _FailingRiskClient(_DummyRiskClient):
    async def calculate_risk(self, payload):
        raise RuntimeError(f"risk failure for {payload['portfolio_id']}")


class _HappyReportingReadService:
    def __init__(self, *, core_query_client, performance_client, risk_client):
        self._core = core_query_client
        self._performance = performance_client
        self._risk = risk_client

    async def get_portfolio_review(self, portfolio_id, request_payload, correlation_id=None):
        await self._core.get_portfolio_summary(portfolio_id, request_payload, correlation_id)
        await self._core.get_asset_allocation(portfolio_id, request_payload, correlation_id)
        await self._core.get_portfolio_transactions(
            portfolio_id,
            {"as_of_date": request_payload["as_of_date"]},
            correlation_id,
        )
        await self._core.get_portfolio_positions(
            portfolio_id,
            {"as_of_date": request_payload["as_of_date"]},
            correlation_id,
        )
        await self._core.get_portfolio_detail(portfolio_id, correlation_id)
        await self._performance.get_workspace_summary(request_payload)
        await self._performance.get_contribution(request_payload)
        await self._risk.calculate_risk(request_payload)
        return {
            "report_id": f"portfolio-review:{portfolio_id}:{request_payload['as_of_date']}",
            "portfolio_id": portfolio_id,
            "as_of_date": request_payload["as_of_date"],
            "contract_version": "v1",
        }


class _ValidationFailureReportingReadService:
    def __init__(self, **_kwargs):
        pass

    async def get_portfolio_review(self, *_args, **_kwargs):
        raise HTTPException(status_code=422, detail="unsupported")


@pytest.mark.asyncio
async def test_capture_service_records_snapshot_and_lineage_for_success(monkeypatch, tmp_path):
    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _HappyReportingReadService,
    )
    ledger, store, job = _create_job(tmp_path, suffix="success")
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.report_job_id == job.job_id
    assert snapshot.supportability_status == "complete"
    assert snapshot.lineage_summary["call_count"] == 8
    calls = store.list_upstream_calls(snapshot.snapshot_id)
    assert len(calls) == 8
    assert {call.service_name for call in calls} == {
        "lotus-core",
        "lotus-performance",
        "lotus-risk",
    }
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_marks_failed_and_persists_failed_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _ValidationFailureReportingReadService,
    )
    ledger, store, job = _create_job(tmp_path, suffix="failed")
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "failed"
    assert record.failure_category == "validation_failed"
    assert record.retry_eligible is False
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.snapshot_payload["capture_status"] == "failed"
    assert snapshot.snapshot_payload["failure_category"] == "validation_failed"
    assert snapshot.supportability_status == "error"
    assert store.list_upstream_calls(snapshot.snapshot_id) == []
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "failed",
    ]


@pytest.mark.asyncio
async def test_capture_service_returns_existing_terminal_or_captured_jobs(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="existing")
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    data_ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    returned_terminal = await service.capture_for_job(data_ready)
    assert returned_terminal == data_ready

    ledger2, store2, job2 = _create_job(tmp_path, suffix="cached")
    store2.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job2.job_id,
            report_type=job2.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job2.portfolio_scope,
            as_of_date=job2.as_of_date,
            snapshot_payload={"report_id": "existing"},
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-core"], "call_count": 1},
            captured_at=datetime.now(UTC),
            correlation_id=job2.correlation_id,
            trace_id=job2.trace_id,
        )
    )
    service2 = PortfolioReviewSnapshotCaptureService(snapshot_store=store2, job_ledger=ledger2)
    replayed = await service2.capture_for_job(job2)
    assert replayed.status == "data_ready"
    assert [event.to_status for event in ledger2.list_status_events(job2.job_id)] == [
        "accepted",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_recording_clients_capture_success_and_failure_paths():
    recorder = _UpstreamRecorder(correlation_id="corr", trace_id="trace")

    class _FailingCoreClient(_DummyCoreClient):
        async def get_portfolio_positions(self, portfolio_id, params, correlation_id=None):
            raise TimeoutError("timed out")

    core = _RecordingCoreQueryClient(_FailingCoreClient(), recorder)
    performance = _RecordingPerformanceClient(_DummyPerformanceClient(), recorder)
    risk = _RecordingRiskClient(_DummyRiskClient(), recorder)

    status_code, payload = await core.get_portfolio_summary(
        "PB_SG_GLOBAL_BAL_001",
        {"as_of_date": "2026-04-22"},
        "corr",
    )
    assert status_code == 200
    assert payload["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"

    with pytest.raises(TimeoutError):
        await core.get_portfolio_positions(
            "PB_SG_GLOBAL_BAL_001",
            {"as_of_date": "2026-04-22"},
            "corr",
        )

    await core.get_asset_allocation("PB_SG_GLOBAL_BAL_001", {"dimension": "asset_class"}, "corr")
    await core.get_portfolio_transactions("PB_SG_GLOBAL_BAL_001", {"page": 1}, "corr")
    await core.get_portfolio_detail("PB_SG_GLOBAL_BAL_001", "corr")
    await performance.get_workspace_summary({"portfolio_id": "PB_SG_GLOBAL_BAL_001"})
    await performance.get_contribution({"portfolio_id": "PB_SG_GLOBAL_BAL_001"})
    await risk.calculate_risk({"portfolio_id": "PB_SG_GLOBAL_BAL_001"})

    calls = recorder.calls
    assert len(calls) == 8
    assert calls[0].endpoint == "/reporting/portfolio-summary/query"
    assert calls[1].failure_category == "timeout"
    assert calls[2].endpoint == "/reporting/asset-allocation/query"
    assert calls[3].endpoint.endswith("/transactions")
    assert calls[4].endpoint.endswith("PB_SG_GLOBAL_BAL_001")
    assert calls[5].service_name == "lotus-performance"
    assert calls[6].service_name == "lotus-performance"
    assert calls[7].service_name == "lotus-risk"


def test_capture_service_helper_classification_and_posture():
    assert _payload_contract_version(None) == "unknown"
    assert _payload_contract_version({"contract_version": " v2 "}) == "v2"
    assert _hash_payload({"a": 1}).startswith("sha256:")

    assert _classify_call(200, {"status": "redacted"}) == (
        "redacted",
        "redacted",
        "redacted",
        "Upstream response content was redacted.",
    )
    assert _classify_call(200, {"status": "unsupported"}) == (
        "not_supported",
        "not_supported",
        "unsupported_input",
        "Upstream service reported that the requested input or capability is not supported.",
    )
    assert _classify_call(503, {"error": "down"}) == (
        "unavailable",
        "unavailable",
        "upstream_unavailable",
        "Upstream service was unavailable while report data was being captured.",
    )
    assert _classify_call(409, {"error": "bad"}) == (
        "error",
        "error",
        "upstream_error",
        "Upstream service returned an error during report data capture.",
    )
    assert _classify_call(200, {"source_unavailable": True}) == (
        "partial",
        "partial",
        "partial_data",
        "Upstream response was accepted but only partially supportable.",
    )
    assert _classify_call(200, {"contract_version": "v1"}) == ("complete", "complete", "none", None)

    recorder = _UpstreamRecorder(correlation_id="corr", trace_id="trace")
    recorder.append_success(
        service_name="lotus-core",
        endpoint="/summary",
        method="POST",
        request_payload={"portfolio_id": "PB1"},
        status_code=200,
        response_payload={"contract_version": "v1"},
        started_at=0.0,
    )
    recorder.append_failure(
        service_name="lotus-risk",
        endpoint="/risk",
        method="POST",
        request_payload={"portfolio_id": "PB1"},
        started_at=0.0,
        exc=httpx.TimeoutException("timeout"),
    )
    summary = _lineage_summary(recorder.calls)
    assert summary["call_count"] == 2
    assert summary["unavailable_call_count"] == 1
    assert _overall_posture(recorder.calls) == "unavailable"
    assert _overall_posture([]) == "error"
    assert (
        _overall_posture(
            [
                recorder.calls[0].__class__(
                    **{**asdict(recorder.calls[0]), "supportability_status": "partial"}
                )
            ]
        )
        == "partial"
    )
    assert (
        _overall_posture(
            [
                recorder.calls[0].__class__(
                    **{**asdict(recorder.calls[0]), "supportability_status": "not_supported"}
                )
            ]
        )
        == "not_supported"
    )
    assert (
        _overall_posture(
            [
                recorder.calls[0].__class__(
                    **{**asdict(recorder.calls[0]), "supportability_status": "redacted"}
                )
            ]
        )
        == "redacted"
    )
    assert recorder.calls[1].to_create_request().response_hash is None


def test_capture_service_request_and_failure_helpers(tmp_path):
    ledger, _store, job = _create_job(tmp_path, suffix="helpers")

    assert _first_portfolio_id(job) == "PB_SG_GLOBAL_BAL_001"
    assert _request_payload(job)["reporting_currency"] == "USD"
    assert _request_payload(job.model_copy(update={"reporting_currency": None})) == {
        "as_of_date": "2026-04-22",
        "sections": ["OVERVIEW", "PERFORMANCE"],
    }

    empty_scope = job.model_copy(update={"portfolio_scope": {"portfolio_ids": []}})
    with pytest.raises(HTTPException, match="portfolio_scope_portfolio_ids_required"):
        _first_portfolio_id(empty_scope)

    assert _map_job_failure(httpx.TimeoutException("timeout")) == (
        "timeout",
        "Upstream report-data capture timed out.",
        True,
    )
    assert _map_job_failure(HTTPException(status_code=503, detail="down")) == (
        "upstream_data_failed",
        "Upstream report-data capture failed.",
        True,
    )
    assert _map_job_failure(HTTPException(status_code=422, detail="unsupported")) == (
        "validation_failed",
        "Requested report inputs were not fully supported.",
        False,
    )
    assert _map_job_failure(RuntimeError("unexpected")) == (
        "upstream_data_failed",
        "Upstream report-data capture failed.",
        True,
    )


@pytest.mark.asyncio
async def test_capture_service_additional_payload_and_failure_branches():
    recorder = _UpstreamRecorder(correlation_id="corr", trace_id="trace")
    assert _payload_contract_version({"contract_version": "   "}) == "v1"
    assert _classify_call(200, None) == ("complete", "complete", "none", None)
    assert _classify_call(200, {"bad": {1, 2, 3}})[0] == "complete"

    performance = _RecordingPerformanceClient(
        _FailingPerformanceClient(),
        recorder,
    )
    risk = _RecordingRiskClient(_FailingRiskClient(), recorder)

    with pytest.raises(RuntimeError, match="workspace failure"):
        await performance.get_workspace_summary({"portfolio_id": "PB_SG_GLOBAL_BAL_001"})
    with pytest.raises(RuntimeError, match="contribution failure"):
        await performance.get_contribution({"portfolio_id": "PB_SG_GLOBAL_BAL_001"})
    with pytest.raises(RuntimeError, match="risk failure"):
        await risk.calculate_risk({"portfolio_id": "PB_SG_GLOBAL_BAL_001"})

    assert [call.failure_category for call in recorder.calls] == [
        "upstream_error",
        "upstream_error",
        "upstream_error",
    ]
    summary = _lineage_summary(recorder.calls)
    assert summary["supportability_status"] == "error"
    assert summary["redacted_call_count"] == 0
    assert summary["not_supported_call_count"] == 0
    assert summary["partial_call_count"] == 0


def test_capture_service_getter_returns_cached_service(monkeypatch):
    class _SentinelStore:
        pass

    class _SentinelLedger:
        pass

    sentinel_store = _SentinelStore()
    sentinel_ledger = _SentinelLedger()

    lineage_service.get_report_input_snapshot_store.cache_clear()
    lineage_service.get_portfolio_review_snapshot_capture_service.cache_clear()
    monkeypatch.setattr(lineage_service, "get_report_input_snapshot_store", lambda: sentinel_store)
    monkeypatch.setattr(lineage_service, "get_report_job_ledger", lambda: sentinel_ledger)

    service = lineage_service.get_portfolio_review_snapshot_capture_service()

    assert isinstance(service, PortfolioReviewSnapshotCaptureService)
    assert service._snapshot_store is sentinel_store
    assert service._job_ledger is sentinel_ledger

    lineage_service.get_portfolio_review_snapshot_capture_service.cache_clear()
