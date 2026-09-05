from __future__ import annotations

import copy
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
    ProposalMemoReportPackage,
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
        "context_content_hash": "sha256:portfolio-memory-context",
        "support_boundary": "BOUNDED_EVENT_REFS_ONLY",
        "event_ref_limit": 12,
        "event_ref_selection_policy": "MOST_RECENT_RELEVANT_FIRST",
        "event_refs_returned": 1,
        "event_refs_omitted": 1,
        "event_refs_truncated": True,
        "event_refs": [
            {
                "event_identity": "lotus-manage:DPM_PROOF_PACK:dpp_001:sha256:proof-pack",
                "event_type": "PROOF_PACK_CREATED",
                "source_system": "lotus-manage",
                "source_type": "DPM_PROOF_PACK",
                "source_id": "dpp_001",
                "content_hash": "sha256:proof-pack",
                "event_time": "2026-05-03T08:59:00Z",
                "event_ref_selection_rank": 1,
                "manage_lookup_id": "pmem_lookup_dpp_001",
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

    async def drawdown_analytics(self, payload):
        return 200, {"contract_version": "v1", "results": {}, "metadata": {}}


class _FailingPerformanceClient(_DummyPerformanceClient):
    async def get_workspace_summary(self, payload):
        raise RuntimeError(f"workspace failure for {payload['portfolio_id']}")

    async def get_contribution(self, payload):
        raise RuntimeError(f"contribution failure for {payload['portfolio_id']}")


class _FailingRiskClient(_DummyRiskClient):
    async def calculate_risk(self, payload):
        raise RuntimeError(f"risk failure for {payload['portfolio_id']}")

    async def drawdown_analytics(self, payload):
        raise RuntimeError("drawdown failure")


class _HappyReportingReadService:
    def __init__(self, *, core_query_client, performance_client, risk_client):
        self._core = core_query_client
        self._performance = performance_client
        self._risk = risk_client

    async def get_portfolio_review(
        self,
        portfolio_id,
        request_payload,
        correlation_id=None,
        admitted_tenant_id=None,
        evidence_posture="ephemeral_composition",
    ):
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
            "reportingCurrency": request_payload.get("reporting_currency") or "USD",
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
        correlation_id="corr-101",
        trace_id="trace-101",
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


class _DummyAiClient:
    def __init__(self, **_kwargs):
        pass

    async def get_accepted_workflow_output(self, run_id, *, tenant_id):
        return 200, {
            "schema_id": "lotus-ai.workflow_pack_run.accepted_output.advisor_brief.v1",
            "run_id": run_id,
            "pack_id": "advisor_brief.pack",
            "pack_version": "v1",
            "task_id": "task_1",
            "request_id": "req_77",
            "tenant_id": tenant_id,
            "workflow_authority_owner": "lotus-performance",
            "review": {"reviewed_by": "advisor-lead-7", "reviewed_at": "2026-04-21T10:00:00Z"},
            "advisor_brief_status": "complete",
            "coverage_state": "full",
            "grounded_summary": "Reviewed summary.",
            "talking_points": [],
            "risks_and_exceptions": [],
            "context": {
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "period": "YTD",
                "as_of_date": "2026-04-22",
                "reporting_currency": "USD",
                "benchmark": None,
            },
            "source_refs": ["performance:workspace-summary"],
            "evidence_types": ["metric_evidence"],
            "content_hash": "0b" * 32,
            "content_hash_algorithm": "sha256",
            # Real lotus-ai responses always carry a complete VALIDATED verdict
            # (ai#231 refuses to publish anything else), so a fixture without
            # one models a response the service cannot produce.
            "output_validation": {
                "validation_state": "VALIDATED",
                "authority": "non_authoritative_ai_output",
                "ruleset_version": "output-validation.v4",
            },
            "notes": [],
        }


class _RejectedAiClient(_DummyAiClient):
    async def get_accepted_workflow_output(self, run_id, *, tenant_id):
        return 409, {"detail": "refused", "metadata": {"reason_code": "run_not_accepted"}}


class _DownAiClient(_DummyAiClient):
    async def get_accepted_workflow_output(self, run_id, *, tenant_id):
        return 503, {"detail": "unavailable"}


def _advisor_commentary_resolution_count(outcome: str, reason: str) -> float:
    from app.reporting_metrics import _ADVISOR_COMMENTARY_RESOLUTIONS_TOTAL

    return _ADVISOR_COMMENTARY_RESOLUTIONS_TOTAL.labels(outcome=outcome, reason=reason)._value.get()


def _patch_portfolio_review_upstreams(monkeypatch, *, ai_client_cls):
    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _HappyReportingReadService,
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.AiClient", ai_client_cls)


def _advisor_commentary_request():
    return _request(
        options={
            "sections": ["OVERVIEW", "PERFORMANCE", "ADVISOR_COMMENTARY"],
            "advisor_brief_run_id": "run_accept_1",
        }
    )


@pytest.mark.asyncio
async def test_capture_service_composes_advisor_commentary_from_accepted_brief(
    monkeypatch, tmp_path
):
    _patch_portfolio_review_upstreams(monkeypatch, ai_client_cls=_DummyAiClient)
    ledger = ReportJobLedger(tmp_path / "jobs-advisor.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-advisor.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_advisor_commentary_request(),
        caller_context=_caller(),
        idempotency_key="idem-advisor-included",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    package = snapshot.snapshot_payload["advisor_commentary_package"]
    assert package["status"] == "included"
    assert package["run_id"] == "run_accept_1"
    assert package["review"]["reviewed_by"] == "advisor-lead-7"
    assert "reviewed by advisor-lead-7" in package["disclosure_text"]
    # The lotus-ai read is durable upstream-call evidence like every other
    # source read.
    calls = store.list_upstream_calls(snapshot.snapshot_id)
    ai_calls = [call for call in calls if call.service_name == "lotus-ai"]
    assert len(ai_calls) == 1
    assert ai_calls[0].endpoint == "/platform/workflow-packs/runs/{run_id}/accepted-output"
    # Lineage carries the brief audit identity (issue #166 acceptance 4).
    assert snapshot.lineage_summary["advisor_commentary_status"] == "included"
    assert snapshot.lineage_summary["advisor_brief_run_id"] == "run_accept_1"
    assert snapshot.lineage_summary["advisor_brief_request_id"] == "req_77"
    assert snapshot.lineage_summary["advisor_brief_reviewed_by"] == "advisor-lead-7"
    assert snapshot.lineage_summary["advisor_brief_content_hash"] == "0b" * 32
    # The INCLUDED brief is evidenced source revision (run_id + content_hash
    # from the accepted-output contract), never a bare lotus-ai entry.
    vector = snapshot.source_revision_vector
    assert vector is not None
    ai_revisions = [
        revision for revision in vector["revisions"] if revision["source_service"] == "lotus-ai"
    ]
    assert ai_revisions == [
        {
            "source_service": "lotus-ai",
            "calculation_run_id": "run_accept_1",
            "content_hash": "0b" * 32,
        }
    ]
    assert _advisor_commentary_resolution_count("included", "none") >= 1.0


@pytest.mark.asyncio
async def test_advisor_commentary_checks_the_effective_snapshot_currency(monkeypatch, tmp_path):
    """When the order omits reporting_currency the snapshot still renders in
    an effective currency derived from the portfolio; a brief asserting a
    different currency must mismatch against THAT, not slip past a None."""

    class _SgdBriefAiClient(_DummyAiClient):
        async def get_accepted_workflow_output(self, run_id, *, tenant_id):
            status_code, payload = await super().get_accepted_workflow_output(
                run_id, tenant_id=tenant_id
            )
            payload["context"]["reporting_currency"] = "SGD"
            return status_code, payload

    _patch_portfolio_review_upstreams(monkeypatch, ai_client_cls=_SgdBriefAiClient)
    ledger = ReportJobLedger(tmp_path / "jobs-advisor-currency.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-advisor-currency.sqlite3")
    request = _advisor_commentary_request().model_copy(update={"reporting_currency": None})
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=_caller(),
        idempotency_key="idem-advisor-currency",
    )
    assert job.reporting_currency is None
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    package = snapshot.snapshot_payload["advisor_commentary_package"]
    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_context_mismatch"
    assert "SGD" in package["detail"]


@pytest.mark.asyncio
async def test_capture_service_closes_advisor_commentary_section_with_reason(monkeypatch, tmp_path):
    _patch_portfolio_review_upstreams(monkeypatch, ai_client_cls=_RejectedAiClient)
    ledger = ReportJobLedger(tmp_path / "jobs-advisor-closed.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-advisor-closed.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_advisor_commentary_request(),
        caller_context=_caller(),
        idempotency_key="idem-advisor-closed",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    # The section closes; the report job proceeds.
    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    package = snapshot.snapshot_payload["advisor_commentary_package"]
    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_not_reviewed"
    assert snapshot.lineage_summary["advisor_commentary_reason_code"] == (
        "advisor_brief_not_reviewed"
    )
    events = [
        event
        for event in ledger.list_status_events(job.job_id)
        if event.event_type == "job_advisor_commentary_unavailable"
    ]
    assert len(events) == 1
    assert events[0].event_payload["reason_code"] == "advisor_brief_not_reviewed"
    assert events[0].event_payload["advisor_brief_run_id"] == "run_accept_1"
    assert _advisor_commentary_resolution_count("unavailable", "advisor_brief_not_reviewed") >= 1.0


@pytest.mark.asyncio
async def test_capture_service_fails_retryable_when_advisor_source_unavailable(
    monkeypatch, tmp_path
):
    _patch_portfolio_review_upstreams(monkeypatch, ai_client_cls=_DownAiClient)
    ledger = ReportJobLedger(tmp_path / "jobs-advisor-down.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-advisor-down.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_advisor_commentary_request(),
        caller_context=_caller(),
        idempotency_key="idem-advisor-down",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    # Retrying can succeed, so the CAPTURE fails retryable instead of the
    # pack silently shipping without a section the caller ordered.
    assert record.status == "failed"
    assert record.failure_category == "upstream_data_failed"
    assert record.retry_eligible is True


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
        "call_count": 1,
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
    # The outcome review LINKS the proof pack it reviewed; the revision must
    # name the served artifact itself, never the linked upstream one.
    vector = snapshot.source_revision_vector
    assert vector is not None
    assert vector["coverage"] == "complete"
    assert len(vector["revisions"]) == 1
    outcome_revision = vector["revisions"][0]
    assert outcome_revision["source_service"] == "lotus-manage"
    assert outcome_revision["source_snapshot_id"] == "dor_001"
    assert outcome_revision["content_hash"] == "sha256:report-input"
    assert outcome_revision["source_product"] == "DPM_OUTCOME_REPORT_INPUT"
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
        "call_count": 1,
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
    # The bounded report-input states its own revision evidence: content
    # hash and artifact id, attributed to the ONE capture-validated
    # participant - so coverage is honestly complete.
    vector = snapshot.source_revision_vector
    assert vector is not None
    assert vector["coverage"] == "complete"
    assert len(vector["revisions"]) == 1
    bounded = vector["revisions"][0]
    assert bounded["source_service"] == "lotus-manage"
    assert bounded["content_hash"] == "sha256:report-input"
    assert bounded["source_snapshot_id"] == "dpp_001"
    assert bounded["source_product"] == "DPM_PROOF_PACK_REPORT_INPUT"
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
        "call_count": 1,
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
    # The standard wave input states its evidence_ref via ref_type/ref_id -
    # the permitted alternative representation - and it must still evidence
    # the vector.
    vector = snapshot.source_revision_vector
    assert vector is not None
    assert vector["coverage"] == "complete"
    assert len(vector["revisions"]) == 1
    wave_revision = vector["revisions"][0]
    assert wave_revision["source_service"] == "lotus-manage"
    assert wave_revision["source_snapshot_id"] == "dwv_001"
    assert wave_revision["content_hash"] == "sha256:wave-report-input"
    assert wave_revision["source_product"] == "DPM_WAVE_REPORT_INPUT"
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
    assert snapshot.lineage_summary["portfolio_memory_context_content_hash"] == (
        "sha256:portfolio-memory-context"
    )
    assert snapshot.lineage_summary["portfolio_memory_event_count"] == 2
    assert snapshot.lineage_summary["portfolio_memory_support_boundary"] == (
        "BOUNDED_EVENT_REFS_ONLY"
    )
    assert snapshot.lineage_summary["portfolio_memory_event_ref_limit"] == 12
    assert snapshot.lineage_summary["portfolio_memory_event_ref_selection_policy"] == (
        "MOST_RECENT_RELEVANT_FIRST"
    )
    assert snapshot.lineage_summary["portfolio_memory_event_refs_returned"] == 1
    assert snapshot.lineage_summary["portfolio_memory_event_refs_omitted"] == 1
    assert snapshot.lineage_summary["portfolio_memory_event_refs_truncated"] is True
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
        "collecting_data",
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
        "collecting_data",
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
        template_publication="development",
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
        "collecting_data",
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
    store2.create_capture(
        snapshot=ReportInputSnapshotCreateRequest(
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
        ),
        upstream_calls=[_recorded_call().to_create_request()],
    )
    provider = _FakePortfolioReviewInputProvider()
    service2 = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store2,
        job_ledger=ledger2,
        portfolio_review_input_provider=provider,
    )
    replayed = await service2.capture_for_job(job2)
    assert replayed.status == "data_ready"
    assert provider.jobs == []
    assert [event.to_status for event in ledger2.list_status_events(job2.job_id)] == [
        "accepted",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_repairs_missing_lineage_after_worker_restart(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="missing-lineage-restart")
    collecting = ledger.mark_collecting_data(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    provider = _FakePortfolioReviewInputProvider()
    existing = store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=provider.snapshot_payload,
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary=_lineage_summary(provider.upstream_calls),
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=provider,
    )

    resumed = await service.capture_for_job(collecting)

    assert resumed.status == "data_ready"
    assert provider.jobs == [job.job_id]
    assert len(store.list_upstream_calls(existing.snapshot_id)) == 8
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "data_ready",
    ]


@pytest.mark.asyncio
async def test_capture_service_fails_closed_when_recollected_payload_changed(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="changed-payload-restart")
    existing = store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload={"report_id": "stale-capture"},
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-core"], "call_count": 1},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    provider = _FakePortfolioReviewInputProvider()
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=provider,
    )

    resumed = await service.capture_for_job(job)

    assert resumed.status == "failed"
    assert resumed.failure_category == "data_incomplete"
    assert provider.jobs == [job.job_id]
    assert store.get_snapshot_by_job(job.job_id) == existing
    assert store.list_upstream_calls(existing.snapshot_id) == []


@pytest.mark.asyncio
async def test_capture_service_fails_closed_for_partial_existing_lineage(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="partial-lineage")
    snapshot = store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload={"report_id": "existing"},
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-core"], "call_count": 2},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    store.create_upstream_calls(
        snapshot_id=snapshot.snapshot_id,
        calls=[_recorded_call().to_create_request()],
    )
    provider = _FakePortfolioReviewInputProvider()
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=provider,
    )

    resumed = await service.capture_for_job(job)

    assert resumed.status == "failed"
    assert resumed.failure_category == "data_incomplete"
    assert provider.jobs == []


@pytest.mark.asyncio
async def test_capture_service_replays_stored_capture_failure_without_marking_ready(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="stored-failure")
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload={
                "capture_status": "failed",
                "failure_category": "validation_failed",
                "failure_message": "Requested report inputs were not fully supported.",
            },
            snapshot_storage_ref=None,
            supportability_status="error",
            completeness_status="error",
            lineage_summary={"source_services": [], "call_count": 0},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    resumed = await service.capture_for_job(job)

    assert resumed.status == "failed"
    assert resumed.failure_category == "validation_failed"
    assert resumed.retry_eligible is False
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "failed",
    ]


@pytest.mark.asyncio
async def test_capture_service_preserves_retry_for_stored_timeout_failure(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="stored-timeout")
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload={
                "capture_status": "failed",
                "failure_category": "timeout",
                "failure_message": "Upstream report-data capture timed out.",
            },
            snapshot_storage_ref=None,
            supportability_status="error",
            completeness_status="error",
            lineage_summary={"source_services": [], "call_count": 0},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )

    resumed = await PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
    ).capture_for_job(job)

    assert resumed.status == "failed"
    assert resumed.failure_category == "timeout"
    assert resumed.retry_eligible is True


@pytest.mark.asyncio
async def test_capture_service_rejects_success_without_source_lineage(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="collecting-restart")
    collecting = ledger.mark_collecting_data(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=_FakePortfolioReviewInputProvider(upstream_calls=[]),
    )

    resumed = await service.capture_for_job(collecting)

    assert resumed.status == "failed"
    assert resumed.failure_category == "data_incomplete"
    assert resumed.failure_message == (
        "Report input capture lineage is incomplete or inconsistent."
    )
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "collecting_data",
        "failed",
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
    drawdown_status, _drawdown_payload = await risk.drawdown_analytics(
        {"portfolio_id": "PB_SG_GLOBAL_BAL_001"}
    )
    assert drawdown_status == 200
    calls = recorder.calls
    assert len(calls) == 9
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
    with pytest.raises(RuntimeError, match="drawdown failure"):
        await risk.drawdown_analytics({"portfolio_id": "PB_SG_GLOBAL_BAL_001"})

    assert [call.failure_category for call in recorder.calls] == [
        "upstream_error",
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


def _proposal_memo_package(**overrides) -> dict:
    package = {
        "package_status": "INCLUDED_ADVISOR_PROPOSAL_MEMO",
        "usage": "REPORT_REQUEST_APPROVED_ADVISOR_MEMO",
        "memo_id": "pmemo_001",
        "memo_version": "proposal-memo.v1",
        "memo_status": "EVIDENCE_READY",
        "proposal_id": "prop_001",
        "proposal_version_no": 3,
        "memo_hash": "sha256:memo",
        "source_input_hash": "sha256:memo-inputs",
        "review": {
            "review_action": "APPROVE_FOR_ADVISOR_USE",
            "reviewed_by": "advisor-123",
            "reviewed_at": "2026-04-22T09:10:00Z",
        },
        "sections": [
            {
                "section_id": "RECOMMENDATION_SUMMARY",
                "title": "Recommendation summary",
                "status": "included",
                "summary": "Rebalance toward quality income.",
                "material_claims": [],
                "evidence_refs": ["prop_001#rec"],
                "reason_codes": [],
            }
        ],
        "client_ready_publication": "BLOCKED",
    }
    package.update(overrides)
    return package


@pytest.mark.asyncio
async def test_proposal_memo_survives_the_complete_durable_journey(monkeypatch, tmp_path):
    """The steering's cross-stage rule, applied to the memo defect itself:

    order -> durable job -> REAL capture -> immutable snapshot (exact
    package and hash) -> REAL render package (advisor_proposal_memo) ->
    lineage naming lotus-advise -> and a changed memo under the same
    idempotency key CONFLICTS instead of silently reusing the old job.
    Nothing here seeds the snapshot by hand - the production capture path
    injects the package or the test fails.
    """

    from app.reporting_jobs.ledger import IdempotencyConflictError
    from app.reporting_render.package_builder import _build_render_package

    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _HappyReportingReadService,
    )
    request = _request(
        requested_output_formats=["pdf"],
        proposal_memo_package=_proposal_memo_package(),
    )
    ledger = ReportJobLedger(tmp_path / "jobs-memo.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-memo.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=_caller(),
        idempotency_key="idem-memo-journey",
    )

    assert job.options["proposal_memo_package"]["memo_hash"] == "sha256:memo"

    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)
    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    package = snapshot.snapshot_payload["proposal_memo_package"]
    # The exact ACCEPTED package is the validated model's dump - the fixture
    # plus the contract's defaulted postures, nothing rewritten or inferred.
    expected = ProposalMemoReportPackage.model_validate(_proposal_memo_package()).model_dump(
        mode="json"
    )
    assert package == expected
    assert snapshot.lineage_summary["proposal_memo_id"] == "pmemo_001"
    assert snapshot.lineage_summary["proposal_memo_hash"] == "sha256:memo"
    assert snapshot.lineage_summary["proposal_memo_review_action"] == "APPROVE_FOR_ADVISOR_USE"
    assert "lotus-advise" in snapshot.lineage_summary["source_services"]

    render_package = _build_render_package(
        job=ledger.get_job(job.job_id),
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_memo_journey",
        snapshot_id=snapshot.snapshot_id,
    )
    memo = render_package["report_data"]["advisor_proposal_memo"]
    assert memo["status"] == "included"
    assert memo["memo_id"] == "pmemo_001"
    assert memo["sections"][0]["section_id"] == "RECOMMENDATION_SUMMARY"

    changed = _request(
        requested_output_formats=["pdf"],
        proposal_memo_package=_proposal_memo_package(
            memo_hash="sha256:memo-changed", source_input_hash="sha256:memo-inputs-changed"
        ),
    )
    with pytest.raises(IdempotencyConflictError):
        ledger.create_portfolio_review_job(
            request=changed,
            caller_context=_caller(),
            idempotency_key="idem-memo-journey",
        )


@pytest.mark.asyncio
async def test_the_memo_is_never_forwarded_to_domain_sources(monkeypatch, tmp_path):
    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)
    captured_payloads: list[dict] = []

    class _RecordingReadService(_HappyReportingReadService):
        async def get_portfolio_review(
            self,
            portfolio_id,
            request_payload,
            correlation_id=None,
            admitted_tenant_id=None,
            evidence_posture="ephemeral_composition",
        ):
            captured_payloads.append(dict(request_payload))
            return await super().get_portfolio_review(portfolio_id, request_payload, correlation_id)

    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _RecordingReadService,
    )
    ledger = ReportJobLedger(tmp_path / "jobs-memo-upstream.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-memo-upstream.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(proposal_memo_package=_proposal_memo_package()),
        caller_context=_caller(),
        idempotency_key="idem-memo-upstream",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    assert captured_payloads, "capture must have called the domain read path"
    for payload in captured_payloads:
        assert "proposal_memo_package" not in payload
        assert "proposal_narrative_package" not in payload


@pytest.mark.asyncio
async def test_risk_attribution_survives_the_complete_durable_journey(monkeypatch, tmp_path):
    """The cross-stage rule applied to the new ordered semantic (#254):

    order (sections includes RISK_ATTRIBUTION) -> durable job -> REAL
    capture -> immutable snapshot carrying the captured block -> REAL render
    package emitting report_data.risk_attribution per the locked contract.
    """

    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)

    captured_block = {
        "source": {
            "service": "lotus-risk",
            "endpoint": "/analytics/risk/historical-attribution",
        },
        "request": {
            "attribution_types": ["TOTAL_RISK"],
            "metrics": ["VOLATILITY"],
            "grouping_dimension": "SECTOR",
        },
        "supportability": {"status": "ready", "notes": []},
        "results": {
            "YTD": {
                "start_date": "2026-01-02",
                "end_date": "2026-04-22",
                "attribution_sets": [
                    {
                        "attribution_type": "TOTAL_RISK",
                        "metric": "VOLATILITY",
                        "grouping_dimension": "SECTOR",
                        "total_value": 0.1253,
                        "reconciled_sum": 0.1249,
                        "residual": 0.0004,
                        "contributors": [
                            {
                                "group_key": "SECTOR_TECH",
                                "group_label": "Technology",
                                "component_contribution": 0.0784,
                                "percent_contribution": 0.6258,
                            },
                            {
                                "group_key": "SECTOR_FIN",
                                "group_label": "Financials",
                                "component_contribution": -0.0112,
                                "percent_contribution": -0.0894,
                            },
                        ],
                        "quality_flags": [],
                    }
                ],
                "error": None,
            }
        },
        "metadata": {
            "metric_unit_semantics": {"VOLATILITY": "decimal_ratio"},
            "benchmark_context": {"requested": False, "reason": "APPLIED"},
            "stateful_active_risk_gate_reason": "none",
        },
    }

    class _AttributionReadService(_HappyReportingReadService):
        async def get_portfolio_review(
            self,
            portfolio_id,
            request_payload,
            correlation_id=None,
            admitted_tenant_id=None,
            evidence_posture="ephemeral_composition",
        ):
            payload = await super().get_portfolio_review(
                portfolio_id, request_payload, correlation_id
            )
            sections = request_payload.get("sections") or []
            if "RISK_ATTRIBUTION" in sections:
                payload = dict(payload)
                payload["riskAttribution"] = captured_block
            return payload

    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _AttributionReadService,
    )

    from app.reporting_render.package_builder import _build_render_package

    ledger = ReportJobLedger(tmp_path / "jobs-risk-attr.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-risk-attr.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(
            requested_output_formats=["pdf"],
            options={"sections": ["OVERVIEW", "RISK_ATTRIBUTION"]},
        ),
        caller_context=_caller(),
        idempotency_key="idem-risk-attribution-journey",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.snapshot_payload["riskAttribution"] == captured_block

    render_package = _build_render_package(
        job=ledger.get_job(job.job_id),
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_risk_attr_journey",
        snapshot_id=snapshot.snapshot_id,
    )
    section = render_package["report_data"]["risk_attribution"]
    only = section["sets"][0]
    assert only["posture"] == "ready"
    assert only["unit"] == "decimal_ratio"
    assert (only["total_value"], only["reconciled_sum"], only["residual"]) == (
        "0.1253",
        "0.1249",
        "0.0004",
    )
    assert [row["group_key"] for row in only["contributors"]] == ["SECTOR_TECH", "SECTOR_FIN"]


@pytest.mark.asyncio
async def test_benchmark_series_survives_the_complete_durable_journey(monkeypatch, tmp_path):
    """The cross-stage rule applied to report#288:

    order -> durable job -> REAL capture -> immutable snapshot carrying the
    source-stated benchmark buckets -> REAL render package emitting
    report_data.benchmark_series per the contract locked with Render."""

    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)

    benchmark_history = [
        {
            "period": "2026-01",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "twr_pct": -1.21,
            "cumulative_twr_pct": -1.21,
        },
        {
            "period": "2026-02",
            "period_start": "2026-02-01",
            "period_end": "2026-02-24",
            "twr_pct": 1.02,
            "cumulative_twr_pct": -0.2,
        },
    ]

    class _BenchmarkReadService(_HappyReportingReadService):
        async def get_portfolio_review(
            self,
            portfolio_id,
            request_payload,
            correlation_id=None,
            admitted_tenant_id=None,
            evidence_posture="ephemeral_composition",
        ):
            payload = await super().get_portfolio_review(
                portfolio_id, request_payload, correlation_id
            )
            payload = dict(payload)
            performance = dict(payload.get("performance") or {})
            performance["benchmark_monthly_history"] = benchmark_history
            performance["benchmark"] = {
                "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
                "requested_benchmark_code": None,
                "comparison_status": "available",
                "return_source": "calculated",
                "benchmark_currency": "USD",
                "reason_code": None,
            }
            payload["performance"] = performance
            return payload

    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _BenchmarkReadService,
    )

    from app.reporting_render.package_builder import _build_render_package

    ledger = ReportJobLedger(tmp_path / "jobs-benchmark-series.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-benchmark-series.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(requested_output_formats=["pdf"]),
        caller_context=_caller(),
        idempotency_key="idem-benchmark-series-journey",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    performance = snapshot.snapshot_payload["performance"]
    assert performance["benchmark_monthly_history"] == benchmark_history

    render_package = _build_render_package(
        job=ledger.get_job(job.job_id),
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_benchmark_series_journey",
        snapshot_id=snapshot.snapshot_id,
    )
    block = render_package["report_data"]["benchmark_series"]
    assert block["posture"] == "ready"
    assert block["benchmark_id"] == "BMK_PB_GLOBAL_BALANCED_60_40"
    assert block["benchmark_currency"] == "USD"
    assert block["return_source"] == "calculated"
    assert [point["period"] for point in block["points"]] == ["2026-01", "2026-02"]
    assert block["points"][1]["cumulative_twr_pct"] == "-0.20%"


@pytest.mark.asyncio
async def test_drawdown_survives_the_complete_durable_journey(monkeypatch, tmp_path):
    """The cross-stage rule applied to report#289:

    order -> durable job -> REAL capture -> immutable snapshot carrying the
    source-owned drawdown block -> REAL render package emitting
    report_data.drawdown per the contract locked with Render."""

    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)

    captured_block = {
        "source": {"service": "lotus-risk", "endpoint": "/analytics/risk/drawdown"},
        "request": {"period": "1Y", "net_or_gross": "NET", "include_underwater_series": True},
        "supportability": {"status": "ready", "notes": []},
        "results": {
            "1Y": {
                "start_date": "2025-02-24",
                "end_date": "2026-02-24",
                "summary": {
                    "max_drawdown": -0.124533,
                    "max_drawdown_peak_date": "2026-01-12",
                    "max_drawdown_trough_date": "2026-02-03",
                    "max_drawdown_recovery_date": None,
                },
                "episodes": [
                    {
                        "episode_id": "dd_0002",
                        "peak_date": "2026-01-12",
                        "trough_date": "2026-02-03",
                        "recovery_date": None,
                        "depth": -0.124533,
                        "days_to_trough": 16,
                    }
                ],
                "underwater_series": [
                    {"date": "2026-01-13", "drawdown": -0.0121},
                    {"date": "2026-02-03", "drawdown": -0.124533},
                ],
                "error": None,
            }
        },
        "metadata": {
            "methodology_version": "drawdown.v1",
            "duration_unit": "BUSINESS_DAYS",
        },
    }

    class _DrawdownReadService(_HappyReportingReadService):
        async def get_portfolio_review(
            self,
            portfolio_id,
            request_payload,
            correlation_id=None,
            admitted_tenant_id=None,
            evidence_posture="ephemeral_composition",
        ):
            payload = await super().get_portfolio_review(
                portfolio_id, request_payload, correlation_id
            )
            payload = dict(payload)
            payload["drawdown"] = captured_block
            return payload

    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _DrawdownReadService,
    )

    from app.reporting_render.package_builder import _build_render_package

    ledger = ReportJobLedger(tmp_path / "jobs-drawdown.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-drawdown.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(requested_output_formats=["pdf"]),
        caller_context=_caller(),
        idempotency_key="idem-drawdown-journey",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.snapshot_payload["drawdown"] == captured_block

    render_package = _build_render_package(
        job=ledger.get_job(job.job_id),
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_drawdown_journey",
        snapshot_id=snapshot.snapshot_id,
    )
    block = render_package["report_data"]["drawdown"]
    assert block["posture"] == "ready"
    assert block["underwater"][1] == {"date": "2026-02-03", "drawdown": "-0.124533"}
    assert block["episodes"][0]["recovery_date"] is None
    assert block["summary"]["max_drawdown"] == "-0.124533"
    assert block["duration_unit"] == "BUSINESS_DAYS"


@pytest.mark.asyncio
async def test_durable_capture_admits_the_jobs_tenant_into_evidence(monkeypatch, tmp_path):
    """The durable path publishes the job's admitted tenant and the
    durable_snapshot posture - never a hardcoded default, and never
    indistinguishable from the synchronous ephemeral composition."""

    monkeypatch.setattr("app.reporting_lineage.capture_service.CoreQueryClient", _DummyCoreClient)
    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.PerformanceClient", _DummyPerformanceClient
    )
    monkeypatch.setattr("app.reporting_lineage.capture_service.RiskClient", _DummyRiskClient)
    seen: dict[str, object] = {}

    class _TenantRecordingReadService(_HappyReportingReadService):
        async def get_portfolio_review(
            self,
            portfolio_id,
            request_payload,
            correlation_id=None,
            admitted_tenant_id=None,
            evidence_posture="ephemeral_composition",
        ):
            seen["admitted_tenant_id"] = admitted_tenant_id
            seen["evidence_posture"] = evidence_posture
            return await super().get_portfolio_review(portfolio_id, request_payload, correlation_id)

    monkeypatch.setattr(
        "app.reporting_lineage.capture_service.ReportingReadService",
        _TenantRecordingReadService,
    )
    ledger = ReportJobLedger(tmp_path / "jobs-tenant.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-tenant.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-tenant-evidence",
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(job)

    assert record.status == "data_ready"
    assert seen["admitted_tenant_id"] == job.tenant_id
    assert seen["evidence_posture"] == "durable_snapshot"


def _stated_review_payload() -> dict:
    """A realistically shaped captured payload: composition timestamps and
    transport metadata beside source-stated revision evidence, so the
    factual boundary is proven against the real capture shape."""

    return {
        "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "as_of_date": "2026-04-22",
        "contract_version": "v1",
        "generated_at": "2026-04-22T09:00:01Z",
        "correlation_id": "corr-instance-a",
        "holdings": {
            "rows": [{"security_id": "SEC1", "market_value": "100.25"}],
            "sourceProduct": {
                "source_service": "lotus-core",
                "product_name": "HoldingsAsOf",
                "product_version": "v1",
                "as_of_date": "2026-04-22",
                "generated_at": "2026-04-22T08:59:59Z",
                "restatement_version": "r1",
                "source_batch_fingerprint": "core-batch-77",
                "snapshot_id": "core-snap-9",
                "content_hash": "sha256:holdings-r1",
                "reconciliation_status": "reconciled",
            },
        },
        "evidence": {
            "trust_metadata": {
                "generated_at": "2026-04-22T09:00:01Z",
                "correlation_id": "corr-instance-a",
            }
        },
    }


def _instance_variant(payload: dict, *, marker: str) -> dict:
    """The SAME facts captured at another instant over another request:
    only report-side capture-instance fields differ."""

    variant = copy.deepcopy(payload)
    variant["generated_at"] = "2026-04-23T11:30:00Z"
    variant["correlation_id"] = f"corr-instance-{marker}"
    variant["evidence"]["trust_metadata"]["generated_at"] = "2026-04-23T11:30:00Z"
    variant["evidence"]["trust_metadata"]["correlation_id"] = f"corr-instance-{marker}"
    return variant


@pytest.mark.asyncio
async def test_identical_facts_share_one_revision_across_capture_instances(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs-revision.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-revision.sqlite3")
    first_job = ledger.create_portfolio_review_job(
        request=_request(), caller_context=_caller(), idempotency_key="idem-rev-a"
    )
    second_job = ledger.create_portfolio_review_job(
        request=_request(), caller_context=_caller(), idempotency_key="idem-rev-b"
    )
    base_payload = _stated_review_payload()

    for job, payload in (
        (first_job, base_payload),
        (second_job, _instance_variant(base_payload, marker="b")),
    ):
        service = PortfolioReviewSnapshotCaptureService(
            snapshot_store=store,
            job_ledger=ledger,
            portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
                snapshot_payload=payload
            ),
        )
        record = await service.capture_for_job(job)
        assert record.status == "data_ready"

    first = store.get_snapshot_by_job(first_job.job_id)
    second = store.get_snapshot_by_job(second_job.job_id)

    assert first.report_revision_id is not None
    assert first.report_revision_id.startswith("rrv3_")
    assert first.report_revision_id == second.report_revision_id
    assert first.factual_content_digest == second.factual_content_digest
    assert first.factual_boundary_version == "fb1"
    # ... while the capture-instance integrity hashes stay distinct: the
    # stored bytes really do differ in their instance fields.
    assert first.snapshot_hash != second.snapshot_hash
    assert "report_revision_id" not in first.snapshot_payload


@pytest.mark.asyncio
async def test_capture_persists_stated_source_revisions_and_honest_coverage(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="revision-vector")
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
            snapshot_payload=_stated_review_payload()
        ),
    )

    await service.capture_for_job(job)

    snapshot = store.get_snapshot_by_job(job.job_id)
    vector = snapshot.source_revision_vector
    assert vector is not None
    # Core stated revision evidence, performance and risk stated none:
    # coverage is computed from that evidence, never asserted complete.
    assert vector["coverage"] == "partial"
    by_service = {}
    for revision in vector["revisions"]:
        by_service.setdefault(revision["source_service"], revision)
    core = by_service["lotus-core"]
    assert core["restatement_version"] == "r1"
    assert core["source_batch_fingerprint"] == "core-batch-77"
    assert core["source_snapshot_id"] == "core-snap-9"
    assert core["content_hash"] == "sha256:holdings-r1"
    assert core["reconciliation_state"] == "reconciled"
    # Silent participants are preserved as bare entries - explicit absence.
    assert set(by_service) == {"lotus-core", "lotus-performance", "lotus-risk"}
    assert list(by_service["lotus-performance"]) == ["source_service"]


@pytest.mark.asyncio
async def test_a_restated_source_cut_changes_the_report_revision(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs-restated.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-restated.sqlite3")
    original_job = ledger.create_portfolio_review_job(
        request=_request(), caller_context=_caller(), idempotency_key="idem-restate-a"
    )
    restated_job = ledger.create_portfolio_review_job(
        request=_request(), caller_context=_caller(), idempotency_key="idem-restate-b"
    )
    restated_payload = copy.deepcopy(_stated_review_payload())
    restated_payload["holdings"]["rows"][0]["market_value"] = "99.75"
    restated_payload["holdings"]["sourceProduct"]["restatement_version"] = "r2"
    restated_payload["holdings"]["sourceProduct"]["content_hash"] = "sha256:holdings-r2"

    for job, payload in (
        (original_job, _stated_review_payload()),
        (restated_job, restated_payload),
    ):
        service = PortfolioReviewSnapshotCaptureService(
            snapshot_store=store,
            job_ledger=ledger,
            portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
                snapshot_payload=payload
            ),
        )
        await service.capture_for_job(job)

    original = store.get_snapshot_by_job(original_job.job_id)
    restated = store.get_snapshot_by_job(restated_job.job_id)

    assert original.report_revision_id != restated.report_revision_id
    assert original.series_digest == restated.series_digest
    assert original.factual_content_digest != restated.factual_content_digest


@pytest.mark.asyncio
async def test_a_failed_capture_mints_no_revision(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="revision-failed")
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
            error=ReportingUpstreamError("core unavailable"),
        ),
    )

    record = await service.capture_for_job(job)

    assert record.status == "failed"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.snapshot_payload.get("capture_status") == "failed"
    assert snapshot.report_revision_id is None
    assert snapshot.factual_content_digest is None
    assert snapshot.source_revision_vector is None


@pytest.mark.asyncio
async def test_a_bounded_capture_persists_the_accepted_snapshot_contract(tmp_path):
    """report#283 finding 6, capture side: the snapshot's input contract is
    the ACCEPTED axis - a deployment that moves the family's bounded-input
    schema must not reinterpret an accepted job's capture. The accepted
    contract also states the family's own schema, never the global
    portfolio-review setting."""

    ledger, store, job = _create_proof_pack_job(tmp_path, suffix="accepted-contract")
    assert job.accepted_document_contract is not None
    assert (
        job.accepted_document_contract["input_snapshot_contract_version"]
        == "dpm_proof_pack_report_input.v1"
    )
    frozen = job.model_copy(
        update={
            "accepted_document_contract": {
                **job.accepted_document_contract,
                "input_snapshot_contract_version": "dpm_proof_pack_report_input.v0-frozen",
            }
        }
    )
    service = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)

    record = await service.capture_for_job(frozen)

    assert record.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert snapshot.report_data_contract_version == "dpm_proof_pack_report_input.v0-frozen"


@pytest.mark.asyncio
async def test_capture_evaluates_and_persists_source_cut_coherence(tmp_path):
    """The verdict is one independently defensible claim persisted beside
    the snapshot: coherent when every stated cut matches the business
    date, incoherent with the offender NAMED when one differs - and it is
    policy-derived, so it lives outside the revision preimage."""

    ledger = ReportJobLedger(tmp_path / "jobs-coherence.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-coherence.sqlite3")
    coherent_job = ledger.create_portfolio_review_job(
        request=_request(), caller_context=_caller(), idempotency_key="idem-coherent"
    )
    incoherent_job = ledger.create_portfolio_review_job(
        request=_request(), caller_context=_caller(), idempotency_key="idem-incoherent"
    )
    stale_payload = copy.deepcopy(_stated_review_payload())
    stale_payload["holdings"]["sourceProduct"]["as_of_date"] = "2026-04-21"

    for job, payload in (
        (coherent_job, _stated_review_payload()),
        (incoherent_job, stale_payload),
    ):
        service = PortfolioReviewSnapshotCaptureService(
            snapshot_store=store,
            job_ledger=ledger,
            portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
                snapshot_payload=payload
            ),
        )
        await service.capture_for_job(job)

    coherent = store.get_snapshot_by_job(coherent_job.job_id).source_cut_coherence
    incoherent = store.get_snapshot_by_job(incoherent_job.job_id).source_cut_coherence
    assert coherent is not None and incoherent is not None
    assert coherent["status"] == "coherent"
    assert coherent["policy_version"] == "scv1"
    assert incoherent["status"] == "incoherent"
    assert "lotus-core=2026-04-21" in incoherent["detail"]


@pytest.mark.asyncio
async def test_a_failed_capture_carries_no_coherence_verdict(tmp_path):
    ledger, store, job = _create_job(tmp_path, suffix="coherence-failed")
    service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
            error=ReportingUpstreamError("core unavailable"),
        ),
    )

    await service.capture_for_job(job)

    assert store.get_snapshot_by_job(job.job_id).source_cut_coherence is None


@pytest.mark.asyncio
async def test_every_capture_states_its_lifecycle_claim(tmp_path):
    """report#283 finding 4: the snapshot names its governing policy
    reference, reproduction availability, and lifecycle authority - stated
    at capture, never enforced. A successful capture supports rerendering
    from the snapshot; a failed capture states NO reproduction rather than
    implying it."""

    ledger = ReportJobLedger(tmp_path / "jobs-lifecycle.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage-lifecycle.sqlite3")
    ok_job = ledger.create_portfolio_review_job(
        request=_request(), caller_context=_caller(), idempotency_key="idem-lc-ok"
    )
    failed_job = ledger.create_portfolio_review_job(
        request=_request(), caller_context=_caller(), idempotency_key="idem-lc-failed"
    )

    ok_service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
            snapshot_payload=_stated_review_payload()
        ),
    )
    await ok_service.capture_for_job(ok_job)
    failing_service = PortfolioReviewSnapshotCaptureService(
        snapshot_store=store,
        job_ledger=ledger,
        portfolio_review_input_provider=_FakePortfolioReviewInputProvider(
            error=ReportingUpstreamError("core unavailable"),
        ),
    )
    await failing_service.capture_for_job(failed_job)

    ok_lifecycle = store.get_snapshot_by_job(ok_job.job_id).lifecycle
    failed_lifecycle = store.get_snapshot_by_job(failed_job.job_id).lifecycle
    assert ok_lifecycle is not None and failed_lifecycle is not None
    assert ok_lifecycle["policy_ref"] == "report-input-snapshot-standard"
    assert ok_lifecycle["reproduction_availability"] == "snapshot_recomposition"
    assert failed_lifecycle["policy_ref"] == "report-input-snapshot-standard"
    assert failed_lifecycle["reproduction_availability"] == "none"
    assert "lotus-archive" in ok_lifecycle["lifecycle_authority"]
