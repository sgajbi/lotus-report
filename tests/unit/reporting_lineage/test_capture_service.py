from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

import httpx
import pytest

from app.application_errors import ReportingUpstreamError, ReportingValidationError
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import (
    OutcomeReviewReportJobRequest,
    PortfolioReviewJobRequest,
    ProofPackReportJobRequest,
    ReportCallerContext,
    WaveReportJobRequest,
)
from app.reporting_lineage import service as lineage_service
from app.reporting_lineage.capture_service import (
    PortfolioReviewInputCapture,
    PortfolioReviewInputCaptureError,
    PortfolioReviewSnapshotCaptureService,
    _classify_call,
    _first_portfolio_id,
    _hash_payload,
    _lineage_summary,
    _map_job_failure,
    _overall_posture,
    _payload_contract_version,
    _RecordedUpstreamCall,
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


def _proposal_narrative_package(**overrides) -> dict:
    package = {
        "package_status": "INCLUDED_REVIEWED_NARRATIVE",
        "usage": "REPORT_REQUEST_APPROVED_ADVISOR_NARRATIVE",
        "proposal_id": "prop_001",
        "proposal_version_no": 3,
        "narrative_id": "pnar_001",
        "narrative_status": "APPROVED_FOR_ADVISOR_USE",
        "audience": "advisor",
        "policy_version": "proposal-narrative-policy.v1",
        "review": {
            "review_id": "pnrev_001",
            "review_state": "APPROVED_FOR_ADVISOR_USE",
            "reviewed_at": "2026-04-22T09:10:00Z",
            "reviewed_by": "advisor-123",
        },
        "source_lineage": {
            "source_narrative_hash": "sha256:narrative",
            "proposal_hash": "sha256:proposal",
        },
        "sections": [
            {
                "section_id": "portfolio_context",
                "title": "Portfolio Context",
                "body": "The portfolio remains aligned to the balanced mandate.",
            }
        ],
        "disclosures": [
            {
                "disclosure_id": "proposal_narrative.advisor_use_only.v1",
                "text": "For advisor use only until the client-ready workflow is approved.",
            }
        ],
    }
    package.update(overrides)
    return package


def _outcome_request(**overrides):
    outcome_report_input = {
        "contract_version": "1.0",
        "outcome_review_id": "dor_001",
        "outcome_review_content_hash": "sha256:outcome-review",
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "proof_pack_id": "dpp_001",
        "review_window": {"start_date": "2026-04-22", "end_date": "2026-04-23"},
        "generated_at": "2026-04-23T09:00:00Z",
        "state": "READY",
        "supportability": {"state": "READY", "reason_codes": ["outcome_review_ready"]},
        "dimensions": [{"dimension": "PERFORMANCE", "state": "READY"}],
        "source_lineage": [
            {
                "source_system": "lotus-manage",
                "source_type": "DPM_OUTCOME_REPORT_INPUT",
                "source_id": "dor_001:dpm_outcome_report_input",
                "content_hash": "sha256:report-input",
            }
        ],
        "source_hashes": {"realized": "sha256:realized"},
        "section_hashes": {"proof_pack": "sha256:proof-pack"},
        "redaction_policy": "NO_RAW_PAYLOADS",
        "retention_policy": "generated-report-standard",
        "evidence_ref": {
            "source_system": "lotus-manage",
            "source_type": "DPM_OUTCOME_REPORT_INPUT",
            "source_id": "dor_001:dpm_outcome_report_input",
            "content_hash": "sha256:report-input",
        },
        "content_hash": "sha256:report-input",
    }
    payload = {
        "outcome_report_input": outcome_report_input,
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }
    payload.update(overrides)
    return OutcomeReviewReportJobRequest.model_validate(payload)


def _proof_pack_request(**overrides):
    proof_pack_report_input = {
        "contract_version": "1.0",
        "proof_pack_id": "dpp_001",
        "proof_pack_content_hash": "sha256:proof-pack",
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "as_of_date": "2026-05-03",
        "generated_at": "2026-05-03T09:00:00Z",
        "state": "READY",
        "supportability": {"status": "READY", "reason_codes": ["proof_pack_ready"]},
        "sections": [
            {
                "section_id": "sec_mandate",
                "section_type": "MANDATE_CONTEXT",
                "state": "READY",
                "title": "Mandate context",
                "summary": "Mandate, model, and policy evidence are aligned.",
                "content_hash": "sha256:section-mandate",
            }
        ],
        "source_hashes": {"mandate": "sha256:mandate"},
        "redaction_policy": "NO_RAW_PAYLOADS",
        "retention_policy": "generated-report-standard",
        "evidence_ref": {
            "source_system": "lotus-manage",
            "source_type": "DPM_PROOF_PACK_REPORT_INPUT",
            "source_id": "dpp_001:dpm_proof_pack_report_input",
            "content_hash": "sha256:report-input",
        },
        "content_hash": "sha256:report-input",
    }
    payload = {
        "proof_pack_report_input": proof_pack_report_input,
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }
    payload.update(overrides)
    return ProofPackReportJobRequest.model_validate(payload)


def _wave_request(**overrides):
    wave_report_input = {
        "contract_version": "1.0",
        "wave_id": "dwv_001",
        "wave_content_hash": "sha256:wave",
        "wave_state": "HANDOFF_READY",
        "trigger_type": "EXPLICIT_PORTFOLIO_LIST",
        "trigger_id": "manual-wave-001",
        "as_of_date": "2026-05-03",
        "generated_at": "2026-05-03T09:00:00Z",
        "supportability": {
            "supportability_state": "ready",
            "reason": "wave_supportability_ready",
        },
        "proof_pack_posture": {
            "linked_item_count": 1,
            "ready_proof_pack_count": 1,
            "degraded_proof_pack_count": 0,
        },
        "items": [
            {
                "wave_item_id": "dwi_001",
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "state": "HANDOFF_READY",
                "proof_pack_id": "dpp_001",
                "proof_pack_state": "READY",
            }
        ],
        "source_refs": [
            {
                "source_system": "lotus-manage",
                "source_type": "DPM_WAVE_REPORT_INPUT",
                "source_id": "dwv_001:dpm_wave_report_input",
                "content_hash": "sha256:wave-report-input",
            }
        ],
        "redaction_policy": "NO_RAW_PAYLOADS",
        "retention_policy": "generated-report-standard",
        "evidence_ref": {
            "source_system": "lotus-manage",
            "ref_type": "DPM_WAVE_REPORT_INPUT",
            "ref_id": "dwv_001:dpm_wave_report_input",
            "content_hash": "sha256:wave-report-input",
        },
        "content_hash": "sha256:wave-report-input",
    }
    payload = {
        "wave_report_input": wave_report_input,
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }
    payload.update(overrides)
    return WaveReportJobRequest.model_validate(payload)


def _portfolio_memory_context() -> dict:
    return {
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "supportability_state": "READY",
        "event_count": 2,
        "content_hash": "sha256:portfolio-memory",
        "event_refs": [
            {
                "event_identity": "lotus-manage:DPM_PROOF_PACK:dpp_001:sha256:proof-pack",
                "event_type": "PROOF_PACK_CREATED",
                "source_system": "lotus-manage",
                "source_type": "DPM_PROOF_PACK",
                "source_id": "dpp_001",
                "content_hash": "sha256:proof-pack",
            }
        ],
    }


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


def _create_outcome_job(tmp_path, *, suffix: str = "outcome-capture"):
    ledger = ReportJobLedger(tmp_path / f"jobs-{suffix}.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / f"lineage-{suffix}.sqlite3")
    job = ledger.create_outcome_review_report_job(
        request=_outcome_request(),
        caller_context=_caller(),
        idempotency_key=f"idem-{suffix}",
    )
    return ledger, store, job


def _create_proof_pack_job(
    tmp_path,
    *,
    suffix: str = "proof-pack-capture",
    request: ProofPackReportJobRequest | None = None,
):
    ledger = ReportJobLedger(tmp_path / f"jobs-{suffix}.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / f"lineage-{suffix}.sqlite3")
    job = ledger.create_proof_pack_report_job(
        request=request or _proof_pack_request(),
        caller_context=_caller(),
        idempotency_key=f"idem-{suffix}",
    )
    return ledger, store, job


def _create_wave_job(tmp_path, *, suffix: str = "wave-capture"):
    ledger = ReportJobLedger(tmp_path / f"jobs-{suffix}.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / f"lineage-{suffix}.sqlite3")
    job = ledger.create_wave_report_job(
        request=_wave_request(),
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
        raise ReportingValidationError("unsupported")


def _recorded_call(
    *,
    service_name: str = "lotus-core",
    endpoint: str = "/reporting/portfolio-summary/query",
) -> _RecordedUpstreamCall:
    return _RecordedUpstreamCall(
        service_name=service_name,
        endpoint=endpoint,
        method="POST",
        contract_version="v1",
        request_payload={"portfolio_id": "PB_SG_GLOBAL_BAL_001"},
        response_payload={"contract_version": "v1"},
        response_ref=None,
        status_code=200,
        latency_ms=1,
        supportability_status="complete",
        completeness_status="complete",
        failure_category="none",
        failure_message=None,
        captured_at=datetime.now(UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
    )


def _portfolio_review_calls() -> list[_RecordedUpstreamCall]:
    return [
        _recorded_call(endpoint="/reporting/portfolio-summary/query"),
        _recorded_call(endpoint="/reporting/asset-allocation/query"),
        _recorded_call(endpoint="/portfolios/PB_SG_GLOBAL_BAL_001/transactions"),
        _recorded_call(endpoint="/portfolios/PB_SG_GLOBAL_BAL_001/positions"),
        _recorded_call(endpoint="/portfolios/PB_SG_GLOBAL_BAL_001"),
        _recorded_call(
            service_name="lotus-performance",
            endpoint="/performance/workspace-summary",
        ),
        _recorded_call(service_name="lotus-performance", endpoint="/performance/contribution"),
        _recorded_call(service_name="lotus-risk", endpoint="/analytics/risk/calculate"),
    ]


class _FakePortfolioReviewInputProvider:
    def __init__(
        self,
        *,
        snapshot_payload: dict | None = None,
        upstream_calls: list[_RecordedUpstreamCall] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.snapshot_payload = snapshot_payload or {
            "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "as_of_date": "2026-04-22",
            "contract_version": "v1",
        }
        self.upstream_calls = (
            _portfolio_review_calls() if upstream_calls is None else upstream_calls
        )
        self.error = error
        self.jobs: list[str] = []

    async def collect_for_job(self, job):
        self.jobs.append(job.job_id)
        if self.error is not None:
            raise PortfolioReviewInputCaptureError(
                original_error=self.error,
                upstream_calls=self.upstream_calls,
            )
        return PortfolioReviewInputCapture(
            snapshot_payload=self.snapshot_payload,
            upstream_calls=self.upstream_calls,
        )


@pytest.mark.asyncio
async def test_capture_service_records_snapshot_and_lineage_for_success(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="success")
    input_provider = _FakePortfolioReviewInputProvider()
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=input_provider,
    )

    record = await service.capture_for_job(job)

    assert input_provider.jobs == [job.job_id]
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
async def test_capture_service_preserves_reviewed_proposal_narrative_package(monkeypatch, tmp_path):
    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _HappyReportingReadService,
    )
    request = _request(proposal_narrative_package=_proposal_narrative_package())
    ledger = ReportJobLedger(tmp_path / "jobs-reviewed-narrative.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-reviewed-narrative.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=_caller(),
        idempotency_key="idem-reviewed-narrative",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    package = snapshot.snapshot_payload["proposal_narrative_package"]
    assert package["narrative_id"] == "pnar_001"
    assert package["review"]["review_state"] == "APPROVED_FOR_ADVISOR_USE"
    assert snapshot.lineage_summary["source_services"] == [
        "lotus-advise",
        "lotus-core",
        "lotus-performance",
        "lotus-risk",
    ]
    assert snapshot.lineage_summary["proposal_narrative_source_hash"] == "sha256:narrative"


@pytest.mark.asyncio
async def test_capture_service_records_outcome_review_snapshot_and_manage_lineage(tmp_path):
    ledger, store, job = _create_outcome_job(tmp_path, suffix="outcome-success")
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.report_type == "outcome_review"
    assert snapshot.report_data_contract_version == "dpm_outcome_report_input.v1"
    assert snapshot.snapshot_payload["outcome_review_id"] == "dor_001"
    assert snapshot.lineage_summary == {
        "source_services": ["lotus-manage"],
        "call_count": 0,
        "supportability_status": "complete",
        "completeness_status": "complete",
        "outcome_review_id": "dor_001",
        "source_hash": "sha256:report-input",
        "portfolio_memory_status": "not_supplied",
    }
    calls = store.list_upstream_calls(snapshot.snapshot_id)
    assert len(calls) == 1
    assert calls[0].service_name == "lotus-manage"
    assert calls[0].endpoint == "/api/v1/rebalance/outcome-reviews/{outcome_review_id}/report-input"
    assert calls[0].request_hash == "sha256:outcome-review"
    assert calls[0].response_hash == "sha256:report-input"
    assert calls[0].response_ref == "dor_001"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_records_proof_pack_snapshot_and_manage_lineage(tmp_path):
    ledger, store, job = _create_proof_pack_job(tmp_path, suffix="proof-pack-success")
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.report_type == "proof_pack"
    assert snapshot.report_data_contract_version == "dpm_proof_pack_report_input.v1"
    assert snapshot.snapshot_payload["proof_pack_id"] == "dpp_001"
    assert snapshot.lineage_summary == {
        "source_services": ["lotus-manage"],
        "call_count": 0,
        "supportability_status": "complete",
        "completeness_status": "complete",
        "proof_pack_id": "dpp_001",
        "source_type": "DPM_PROOF_PACK_REPORT_INPUT",
        "source_hash": "sha256:report-input",
        "portfolio_memory_status": "not_supplied",
    }
    calls = store.list_upstream_calls(snapshot.snapshot_id)
    assert len(calls) == 1
    assert calls[0].service_name == "lotus-manage"
    assert calls[0].endpoint == "/api/v1/rebalance/proof-packs/{proof_pack_id}/report-input"
    assert calls[0].request_hash == "sha256:proof-pack"
    assert calls[0].response_hash == "sha256:report-input"
    assert calls[0].response_ref == "dpp_001:dpm_proof_pack_report_input"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_rejects_spoofed_idea_source_authority(tmp_path):
    proof_pack_input = _proof_pack_request().proof_pack_report_input.model_dump(mode="json")
    request = _proof_pack_request(
        proof_pack_report_input={
            **proof_pack_input,
            "evidence_ref": {
                "source_system": "lotus-idea",
                "source_type": "DPM_PROOF_PACK_REPORT_INPUT",
                "source_id": "spoofed-idea-source",
                "content_hash": "sha256:report-input",
            },
        }
    )
    ledger, store, job = _create_proof_pack_job(
        tmp_path,
        suffix="proof-pack-spoofed-idea-source",
        request=request,
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.lineage_summary["source_services"] == ["lotus-manage"]
    calls = store.list_upstream_calls(snapshot.snapshot_id)
    assert calls[0].service_name == "lotus-manage"
    assert calls[0].endpoint == "/api/v1/rebalance/proof-packs/{proof_pack_id}/report-input"
    assert calls[0].method == "GET"
    assert calls[0].contract_version == "DpmProofPackReportInput.1.0"


@pytest.mark.asyncio
async def test_capture_service_records_idea_materialization_lineage_as_post(tmp_path):
    proof_pack_input = _proof_pack_request().proof_pack_report_input.model_dump(mode="json")
    request = _proof_pack_request(
        proof_pack_report_input={
            **proof_pack_input,
            "evidence_ref": {
                "source_system": "lotus-idea",
                "source_type": "LOTUS_IDEA_EVIDENCE_PACK_REPORT_INPUT",
                "source_id": "irep_001:lotus_idea_evidence_pack_report_input",
                "content_hash": "sha256:report-input",
            },
        }
    )
    ledger, store, job = _create_proof_pack_job(
        tmp_path,
        suffix="proof-pack-idea-materialization-source",
        request=request,
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.lineage_summary["source_services"] == ["lotus-idea"]
    assert snapshot.lineage_summary["source_type"] == "LOTUS_IDEA_EVIDENCE_PACK_REPORT_INPUT"
    calls = store.list_upstream_calls(snapshot.snapshot_id)
    assert calls[0].service_name == "lotus-idea"
    assert calls[0].endpoint == "/reports/idea-evidence-packs/materializations"
    assert calls[0].method == "POST"
    assert calls[0].contract_version == "LotusIdeaEvidencePackReportInput.1.0"


@pytest.mark.asyncio
async def test_capture_service_records_wave_snapshot_and_manage_lineage(tmp_path):
    ledger, store, job = _create_wave_job(tmp_path, suffix="wave-success")
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.report_type == "rebalance_wave"
    assert snapshot.report_data_contract_version == "dpm_wave_report_input.v1"
    assert snapshot.snapshot_payload["wave_id"] == "dwv_001"
    assert snapshot.lineage_summary == {
        "source_services": ["lotus-manage"],
        "call_count": 0,
        "supportability_status": "complete",
        "completeness_status": "complete",
        "wave_id": "dwv_001",
        "source_hash": "sha256:wave-report-input",
        "portfolio_memory_status": "not_supplied",
    }
    calls = store.list_upstream_calls(snapshot.snapshot_id)
    assert len(calls) == 1
    assert calls[0].service_name == "lotus-manage"
    assert calls[0].endpoint == "/api/v1/rebalance/waves/{wave_id}/report-input"
    assert calls[0].request_hash == "sha256:wave"
    assert calls[0].response_hash == "sha256:wave-report-input"
    assert calls[0].response_ref == "dwv_001"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_records_portfolio_memory_lineage_without_recomputing(tmp_path):
    payload = _proof_pack_request().model_dump(mode="json")
    payload["proof_pack_report_input"]["portfolio_memory_context"] = _portfolio_memory_context()
    request = ProofPackReportJobRequest.model_validate(payload)
    ledger, store, job = _create_proof_pack_job(
        tmp_path,
        suffix="proof-pack-portfolio-memory",
        request=request,
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.snapshot_payload["portfolio_memory_context"]["content_hash"] == (
        "sha256:portfolio-memory"
    )
    assert snapshot.lineage_summary["portfolio_memory_status"] == "supplied"
    assert snapshot.lineage_summary["portfolio_memory_content_hash"] == "sha256:portfolio-memory"
    assert snapshot.lineage_summary["portfolio_memory_event_count"] == 2
    assert snapshot.lineage_summary["portfolio_memory_event_ref_count"] == 1


@pytest.mark.asyncio
async def test_capture_service_reuses_existing_proof_pack_snapshot(tmp_path):
    ledger, store, job = _create_proof_pack_job(tmp_path, suffix="proof-pack-existing")
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type="proof_pack",
            report_data_contract_version="dpm_proof_pack_report_input.v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=job.options["proof_pack_report_input"],
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-manage"], "call_count": 0},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_reuses_existing_wave_snapshot(tmp_path):
    ledger, store, job = _create_wave_job(tmp_path, suffix="wave-existing")
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type="rebalance_wave",
            report_data_contract_version="dpm_wave_report_input.v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=job.options["wave_report_input"],
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-manage"], "call_count": 0},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_returns_terminal_proof_pack_job_without_mutation(tmp_path):
    ledger, store, job = _create_proof_pack_job(tmp_path, suffix="proof-pack-terminal")
    ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    completed = ledger.mark_completed(
        job_id=ready.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
        render_job_id="rdr-proof-pack",
        output_format="pdf",
        template_id="proof-pack",
        template_version="v1",
        artifact_sha256="sha256:artifact",
        bounded_determinism_fingerprint="fingerprint",
        runtime_engine="typst",
        runtime_engine_version="0.14.2",
        render_duration_ms=500,
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(completed)

    assert record.status == "completed"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "data_ready",
        "completed",
    ]


@pytest.mark.asyncio
async def test_capture_service_returns_terminal_wave_job_without_mutation(tmp_path):
    ledger, store, job = _create_wave_job(tmp_path, suffix="wave-terminal")
    data_ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(data_ready)

    assert record.status == "data_ready"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_fails_proof_pack_without_report_input(tmp_path):
    ledger, store, job = _create_proof_pack_job(tmp_path, suffix="proof-pack-missing-input")
    malformed_job = job.model_copy(update={"options": {}})
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(malformed_job)

    assert record.status == "failed"
    assert record.failure_category == "validation_failed"
    assert record.failure_message == "Proof-pack report input was not present in the report job."
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "failed",
    ]


@pytest.mark.asyncio
async def test_capture_service_fails_wave_without_report_input(tmp_path):
    ledger, store, job = _create_wave_job(tmp_path, suffix="wave-missing-input")
    malformed_job = job.model_copy(update={"options": {}})
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(malformed_job)

    assert record.status == "failed"
    assert record.failure_category == "validation_failed"
    assert record.failure_message == "Wave report input was not present in the report job."
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "failed",
    ]


@pytest.mark.asyncio
async def test_capture_service_reuses_existing_outcome_review_snapshot(tmp_path):
    ledger, store, job = _create_outcome_job(tmp_path, suffix="outcome-existing")
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type="outcome_review",
            report_data_contract_version="dpm_outcome_report_input.v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=job.options["outcome_report_input"],
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-manage"], "call_count": 0},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_fails_outcome_review_without_report_input(tmp_path):
    ledger, store, job = _create_outcome_job(tmp_path, suffix="outcome-missing-input")
    malformed_job = job.model_copy(update={"options": {}})
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(malformed_job)

    assert record.status == "failed"
    assert record.failure_category == "validation_failed"
    assert (
        record.failure_message == "Outcome-review report input was not present in the report job."
    )
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "failed",
    ]


@pytest.mark.asyncio
async def test_capture_service_marks_failed_and_persists_failed_snapshot(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="failed")
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
            upstream_calls=[],
            error=ReportingValidationError("unsupported"),
        ),
    )

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
    with pytest.raises(ReportingValidationError, match="portfolio_scope_portfolio_ids_required"):
        _first_portfolio_id(empty_scope)

    assert _map_job_failure(httpx.TimeoutException("timeout")) == (
        "timeout",
        "Upstream report-data capture timed out.",
        True,
    )
    assert _map_job_failure(ReportingUpstreamError("down")) == (
        "upstream_data_failed",
        "Upstream report-data capture failed.",
        True,
    )
    assert _map_job_failure(ReportingValidationError("unsupported")) == (
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

    class _SentinelInputProvider:
        pass

    sentinel_store = _SentinelStore()
    sentinel_ledger = _SentinelLedger()
    sentinel_input_provider = _SentinelInputProvider()

    lineage_service.get_report_input_snapshot_store.cache_clear()
    lineage_service.get_portfolio_review_snapshot_capture_service.cache_clear()
    monkeypatch.setattr(lineage_service, "get_report_input_snapshot_store", lambda: sentinel_store)
    monkeypatch.setattr(lineage_service, "get_report_job_ledger", lambda: sentinel_ledger)
    monkeypatch.setattr(
        lineage_service,
        "ReportingReadPortfolioReviewInputProvider",
        lambda: sentinel_input_provider,
    )

    service = lineage_service.get_portfolio_review_snapshot_capture_service()

    assert isinstance(service, PortfolioReviewSnapshotCaptureService)
    assert service._snapshot_store is sentinel_store
    assert service._job_ledger is sentinel_ledger
    assert service._portfolio_review_input_provider is sentinel_input_provider

    lineage_service.get_portfolio_review_snapshot_capture_service.cache_clear()
