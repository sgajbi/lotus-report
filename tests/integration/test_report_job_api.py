import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import ReportJobListFilters
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
from app.reporting_jobs.worker import ReportJobWorker
from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportUpstreamCallCreateRequest,
)
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.regenerate_service import (
    PortfolioReviewRegenerateService,
    get_portfolio_review_regenerate_service,
)
from app.reporting_render.replay_service import (
    PortfolioReviewReplayService,
    get_portfolio_review_replay_service,
)
from app.reporting_render.rerender_service import (
    PortfolioReviewRerenderService,
    get_portfolio_review_rerender_service,
)
from app.reporting_render.service import (
    PortfolioReviewRenderOrchestrationService,
    get_portfolio_review_render_orchestration_service,
)
from app.routers.report_jobs import get_report_lineage_store


def _client(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    lineage_store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    app.dependency_overrides[get_report_lineage_store] = lambda: lineage_store
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        _FakeCaptureService(ledger, lineage_store)
    )
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        _FakeRenderService()
    )
    return TestClient(app), ledger, lineage_store


def _clear_overrides():
    app.dependency_overrides.clear()


def _run_pending_jobs(ledger: ReportJobLedger) -> None:
    capture_factory = app.dependency_overrides[get_portfolio_review_snapshot_capture_service]
    render_factory = app.dependency_overrides[get_portfolio_review_render_orchestration_service]
    worker = ReportJobWorker(
        work_ledger=ledger,
        execution_service=ReportJobExecutionService(
            report_job_ledger=ledger,
            capture_service=capture_factory(),
            render_service=render_factory(),
        ),
    )
    asyncio.run(
        worker.run_once(
            worker_id="integration-report-job-worker",
            max_items=100,
            lease_seconds=60,
        )
    )


def _payload():
    return {
        "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "as_of_date": "2026-04-22",
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {
            "sections": ["OVERVIEW", "PERFORMANCE"],
            "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
        },
    }


def _proposal_narrative_package():
    return {
        "package_status": "INCLUDED_REVIEWED_NARRATIVE",
        "usage": "REPORT_REQUEST_APPROVED_ADVISOR_NARRATIVE",
        "proposal_id": "prop_001",
        "proposal_version_no": 3,
        "narrative_id": "pnar_001",
        "narrative_status": "APPROVED_FOR_ADVISOR_USE",
        "generation_mode": "GOVERNED_AI_ASSISTED",
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
            "proposal_version_hash": "sha256:proposal-version",
        },
        "sections": [
            {
                "section_id": "portfolio_context",
                "title": "Portfolio Context",
                "body": "The portfolio remains aligned to the balanced mandate.",
                "source_refs": [{"source_system": "lotus-advise", "source_id": "prop_001"}],
            }
        ],
        "disclosures": [
            {
                "disclosure_id": "proposal_narrative.advisor_use_only.v1",
                "text": "For advisor use only until the client-ready workflow is approved.",
            }
        ],
        "guardrail_results": [{"guardrail_id": "no_trade_instruction", "status": "passed"}],
        "limitations": [{"limitation_id": "advisor_use_only", "status": "active"}],
        "execution_boundary": {"client_distribution_allowed": False},
    }


def _outcome_payload():
    return {
        "outcome_report_input": {
            "contract_version": "1.0",
            "outcome_review_id": "dor_001",
            "outcome_review_content_hash": "sha256:outcome-review",
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "mandate_id": "mandate_001",
            "rebalance_run_id": "run_001",
            "proof_pack_id": "dpp_001",
            "wave_id": "wave_001",
            "review_window": {"start_date": "2026-04-22", "end_date": "2026-04-23"},
            "generated_at": "2026-04-23T09:00:00Z",
            "report_title": "Post-Trade Outcome Review - PB_SG_GLOBAL_BAL_001",
            "report_audience": ["portfolio_manager", "cio_office", "audit"],
            "state": "READY",
            "overall_outcome": "Execution outcome aligned with pre-trade proof.",
            "variance_summary": {"tracking_error": "0.12"},
            "supportability": {"state": "READY", "reason_codes": ["outcome_review_ready"]},
            "dimensions": [
                {
                    "dimension": "PERFORMANCE",
                    "state": "READY",
                    "reason_code": "performance_realized",
                    "expected": "4.10",
                    "realized": "4.22",
                    "variance": "0.12",
                    "explanation": "Realized performance exceeded expected performance.",
                    "source_refs": [],
                    "supportability": {
                        "state": "READY",
                        "reason_codes": ["performance_realized"],
                    },
                }
            ],
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
        },
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }


def _proof_pack_payload():
    return {
        "proof_pack_report_input": {
            "contract_version": "1.0",
            "proof_pack_id": "dpp_001",
            "proof_pack_content_hash": "sha256:proof-pack",
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "mandate_id": "MANDATE_PB_SG_GLOBAL_BAL_001",
            "as_of_date": "2026-05-03",
            "generated_at": "2026-05-03T09:00:00Z",
            "report_title": "Pre-Trade Proof Pack - PB_SG_GLOBAL_BAL_001",
            "report_audience": ["portfolio_manager", "investment_control", "audit"],
            "state": "READY",
            "decision_summary": {
                "recommended_action": "approve_rebalance",
                "rationale": "Mandate drift and source readiness support rebalance approval.",
            },
            "supportability": {"status": "READY", "reason_codes": ["proof_pack_ready"]},
            "sections": [
                {
                    "section_id": "sec_mandate",
                    "section_type": "MANDATE_CONTEXT",
                    "state": "READY",
                    "title": "Mandate context",
                    "summary": "Mandate, model, and policy evidence are aligned.",
                    "reason_codes": ["mandate_context_ready"],
                    "facts": {},
                    "metrics": {},
                    "evidence_refs": [],
                    "source_refs": [],
                    "content_hash": "sha256:section-mandate",
                }
            ],
            "markdown_summary": "# Pre-Trade Proof Pack",
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
        },
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }


def _wave_payload():
    return {
        "wave_report_input": {
            "contract_version": "1.0",
            "wave_id": "dwv_001",
            "wave_content_hash": "sha256:wave",
            "wave_state": "HANDOFF_READY",
            "trigger_type": "EXPLICIT_PORTFOLIO_LIST",
            "trigger_id": "manual-wave-001",
            "trigger_rationale": "Review explicit affected portfolio list.",
            "as_of_date": "2026-05-03",
            "generated_at": "2026-05-03T09:00:00Z",
            "report_title": "Rebalance Wave Evidence - dwv_001",
            "report_audience": ["portfolio_manager", "operations", "audit"],
            "aggregate_metrics": {
                "item_count": 1,
                "state_counts": {"HANDOFF_READY": 1},
                "ready_item_count": 1,
                "blocked_item_count": 0,
            },
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
                    "mandate_id": "MANDATE_PB_SG_GLOBAL_BAL_001",
                    "model_portfolio_id": "MODEL_PB_SG_GLOBAL_BAL_DPM",
                    "state": "HANDOFF_READY",
                    "reason_codes": ["WAVE_ITEM_HANDOFF_READY"],
                    "selected_alternative_id": "alt_min_turnover",
                    "proof_pack_id": "dpp_001",
                    "proof_pack_state": "READY",
                    "source_refs": [],
                    "diagnostics": {"external_execution_claimed": False},
                }
            ],
            "events": [],
            "handoff_refs": [],
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
            "external_execution_claimed": False,
            "evidence_ref": {
                "source_system": "lotus-manage",
                "ref_type": "DPM_WAVE_REPORT_INPUT",
                "ref_id": "dwv_001:dpm_wave_report_input",
                "content_hash": "sha256:wave-report-input",
            },
            "content_hash": "sha256:wave-report-input",
        },
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {"retention_policy_id": "generated-report-standard"},
    }


def _headers(idempotency_key="portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22"):
    return {
        "Idempotency-Key": idempotency_key,
        "X-Actor-Id": "advisor-123",
        "X-Caller-Application": "lotus-gateway",
        "X-Tenant-Id": "tenant-sg",
        "X-Region": "APAC",
        "X-Booking-Center-Code": "SG",
        "X-Role": "advisor",
        "X-Correlation-ID": "corr-report-job-1",
        "X-Trace-ID": "trace-report-job-1",
    }


def test_portfolio_review_job_rejects_unpublished_ordering_configuration(tmp_path):
    client, _, _ = _client(tmp_path)
    payload = _payload()
    payload["options"] = {"sections": ["CLIENT_STATEMENT"]}
    try:
        response = client.post(
            "/reports/portfolio-reviews",
            json=payload,
            headers=_headers("invalid-report-configuration"),
        )
    finally:
        _clear_overrides()

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "unsupported_report_section",
            "message": "One or more selected report section values are not available.",
        }
    }


def test_source_workflow_rejects_unknown_output_format_before_job_creation(tmp_path):
    client, ledger, _ = _client(tmp_path)
    payload = _proof_pack_payload()
    payload["requested_output_formats"] = ["docx"]
    try:
        response = client.post(
            "/reports/proof-packs",
            json=payload,
            headers=_headers("invalid-proof-pack-format"),
        )
    finally:
        _clear_overrides()

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_report_output_format"
    assert ledger.list_jobs(filters=ReportJobListFilters(limit=10)) == []


class _FakeCaptureService:
    def __init__(self, ledger: ReportJobLedger, lineage_store: ReportInputSnapshotStore):
        self._ledger = ledger
        self._lineage_store = lineage_store
        self.calls = 0

    async def capture_for_job(self, job):
        self.calls += 1
        if job.report_type == "proof_pack":
            self._ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
            proof_pack_report_input = job.options["proof_pack_report_input"]
            source_ref = proof_pack_report_input.get("evidence_ref", {})
            if not isinstance(source_ref, dict):
                source_ref = {}
            source_system = str(source_ref.get("source_system") or "lotus-manage")
            source_type = str(source_ref.get("source_type") or "DPM_PROOF_PACK_REPORT_INPUT")
            source_id = str(
                source_ref.get("source_id")
                or proof_pack_report_input.get("proof_pack_id")
                or job.job_id
            )
            source_endpoint = (
                "/reports/idea-evidence-packs/materializations"
                if source_system == "lotus-idea"
                else "/api/v1/rebalance/proof-packs/{proof_pack_id}/report-input"
            )
            source_contract_version = (
                "LotusIdeaEvidencePackReportInput.1.0"
                if source_system == "lotus-idea"
                else "DpmProofPackReportInput.1.0"
            )
            snapshot = self._lineage_store.create_snapshot(
                ReportInputSnapshotCreateRequest(
                    report_job_id=job.job_id,
                    report_type=job.report_type,
                    report_data_contract_version="dpm_proof_pack_report_input.v1",
                    portfolio_scope=job.portfolio_scope,
                    as_of_date=job.as_of_date,
                    snapshot_payload=proof_pack_report_input,
                    snapshot_storage_ref=None,
                    supportability_status="complete",
                    completeness_status="complete",
                    lineage_summary={
                        "source_services": [source_system],
                        "call_count": 1,
                        "supportability_status": "complete",
                        "source_type": source_type,
                    },
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            )
            self._lineage_store.create_upstream_calls(
                snapshot_id=snapshot.snapshot_id,
                calls=[
                    ReportUpstreamCallCreateRequest(
                        service_name=source_system,
                        endpoint=source_endpoint,
                        method="GET",
                        contract_version=source_contract_version,
                        request_hash="sha256:proof-pack",
                        response_hash="sha256:report-input",
                        response_ref=source_id,
                        status_code=200,
                        latency_ms=0,
                        supportability_status="complete",
                        completeness_status="complete",
                        failure_category="none",
                        failure_message=None,
                        captured_at=datetime.now(UTC),
                        correlation_id=job.correlation_id,
                        trace_id=job.trace_id,
                    )
                ],
            )
            return self._ledger.mark_data_ready(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        if job.report_type == "outcome_review":
            self._ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
            outcome_report_input = job.options["outcome_report_input"]
            snapshot = self._lineage_store.create_snapshot(
                ReportInputSnapshotCreateRequest(
                    report_job_id=job.job_id,
                    report_type=job.report_type,
                    report_data_contract_version="dpm_outcome_report_input.v1",
                    portfolio_scope=job.portfolio_scope,
                    as_of_date=job.as_of_date,
                    snapshot_payload=outcome_report_input,
                    snapshot_storage_ref=None,
                    supportability_status="complete",
                    completeness_status="complete",
                    lineage_summary={
                        "source_services": ["lotus-manage"],
                        "call_count": 1,
                        "supportability_status": "complete",
                    },
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            )
            self._lineage_store.create_upstream_calls(
                snapshot_id=snapshot.snapshot_id,
                calls=[
                    ReportUpstreamCallCreateRequest(
                        service_name="lotus-manage",
                        endpoint="/api/v1/rebalance/outcome-reviews/{outcome_review_id}/report-input",
                        method="GET",
                        contract_version="DpmOutcomeReportInput.1.0",
                        request_hash="sha256:outcome-review",
                        response_hash="sha256:report-input",
                        response_ref="dor_001",
                        status_code=200,
                        latency_ms=0,
                        supportability_status="complete",
                        completeness_status="complete",
                        failure_category="none",
                        failure_message=None,
                        captured_at=datetime.now(UTC),
                        correlation_id=job.correlation_id,
                        trace_id=job.trace_id,
                    )
                ],
            )
            return self._ledger.mark_data_ready(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        if job.report_type == "rebalance_wave":
            self._ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
            wave_report_input = job.options["wave_report_input"]
            snapshot = self._lineage_store.create_snapshot(
                ReportInputSnapshotCreateRequest(
                    report_job_id=job.job_id,
                    report_type=job.report_type,
                    report_data_contract_version="dpm_wave_report_input.v1",
                    portfolio_scope=job.portfolio_scope,
                    as_of_date=job.as_of_date,
                    snapshot_payload=wave_report_input,
                    snapshot_storage_ref=None,
                    supportability_status="complete",
                    completeness_status="complete",
                    lineage_summary={
                        "source_services": ["lotus-manage"],
                        "call_count": 1,
                        "supportability_status": "complete",
                    },
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            )
            self._lineage_store.create_upstream_calls(
                snapshot_id=snapshot.snapshot_id,
                calls=[
                    ReportUpstreamCallCreateRequest(
                        service_name="lotus-manage",
                        endpoint="/api/v1/rebalance/waves/{wave_id}/report-input",
                        method="GET",
                        contract_version="DpmWaveReportInput.1.0",
                        request_hash="sha256:wave",
                        response_hash=str(wave_report_input["content_hash"]),
                        response_ref="dwv_001",
                        status_code=200,
                        latency_ms=0,
                        supportability_status="complete",
                        completeness_status="complete",
                        failure_category="none",
                        failure_message=None,
                        captured_at=datetime.now(UTC),
                        correlation_id=job.correlation_id,
                        trace_id=job.trace_id,
                    )
                ],
            )
            return self._ledger.mark_data_ready(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        self._ledger.mark_collecting_data(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        snapshot_payload = {
            "report_id": (
                "portfolio-review:"
                f"{job.portfolio_scope['portfolio_ids'][0]}:"
                f"{job.as_of_date.isoformat()}"
            ),
            "portfolio_id": job.portfolio_scope["portfolio_ids"][0],
            "as_of_date": job.as_of_date.isoformat(),
            "capture_sequence": self.calls,
        }
        proposal_narrative_package = job.options.get("proposal_narrative_package")
        source_services = ["lotus-core", "lotus-performance", "lotus-risk"]
        if isinstance(proposal_narrative_package, dict):
            snapshot_payload["proposal_narrative_package"] = proposal_narrative_package
            source_services.append("lotus-advise")
        snapshot = self._lineage_store.create_snapshot(
            ReportInputSnapshotCreateRequest(
                report_job_id=job.job_id,
                report_type=job.report_type,
                report_data_contract_version="v1",
                portfolio_scope=job.portfolio_scope,
                as_of_date=job.as_of_date,
                snapshot_payload=snapshot_payload,
                snapshot_storage_ref=None,
                supportability_status="complete",
                completeness_status="complete",
                lineage_summary={
                    "source_services": source_services,
                    "call_count": 1,
                    "supportability_status": "complete",
                    "partial_call_count": 0,
                    "unavailable_call_count": 0,
                    "not_supported_call_count": 0,
                    "redacted_call_count": 0,
                },
                captured_at=datetime.now(UTC),
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        )
        self._lineage_store.create_upstream_calls(
            snapshot_id=snapshot.snapshot_id,
            calls=[
                ReportUpstreamCallCreateRequest(
                    service_name="lotus-core",
                    endpoint="/reporting/portfolio-summary/query",
                    method="POST",
                    contract_version="v1",
                    request_hash="sha256:req",
                    response_hash="sha256:resp",
                    response_ref=None,
                    status_code=200,
                    latency_ms=184,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            ],
        )
        return self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )


class _FakeRenderService:
    async def render_for_job(self, job):
        return job


class _CountingRenderService:
    def __init__(self):
        self.calls = 0

    async def render_for_job(self, job):
        self.calls += 1
        return job


class _RerenderRenderClient:
    def __init__(self, *, status_code=201, payload=None):
        self.status_code = status_code
        self.payload = payload
        self.payloads = []

    async def submit_render_package(self, payload, **kwargs):
        self.payloads.append(payload)
        if self.payload is not None:
            return self.status_code, self.payload
        return self.status_code, {
            "status": "rendered",
            "render_job_id": payload["render_job_id"],
            "artifact_sha256": "sha256:rerender-artifact",
            "bounded_determinism_fingerprint": "fingerprint-rerender",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 731,
            "artifact_base64": "JVBERi0xLjQ=",
        }


class _RerenderArchiveClient:
    def __init__(self, *, status_code=201, payload=None):
        self.status_code = status_code
        self.payload = payload or {"document_id": "doc_report_job_pdf_correction"}
        self.payloads = []

    async def archive_document(self, payload, **kwargs):
        self.payloads.append(payload)
        return self.status_code, self.payload


def _install_rerender_service(ledger, lineage_store, render_client, archive_client):
    service = PortfolioReviewRerenderService(
        render_client=render_client,
        archive_client=archive_client,
        snapshot_store=lineage_store,
        ledger=ledger,
    )
    app.dependency_overrides[get_portfolio_review_rerender_service] = lambda: service
    return service


def _install_regenerate_service(
    ledger, lineage_store, capture_service, render_client, archive_client
):
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=render_client,
        archive_client=archive_client,
        snapshot_store=lineage_store,
        job_ledger=ledger,
    )
    service = PortfolioReviewRegenerateService(
        ledger=ledger,
        snapshot_store=lineage_store,
        capture_service=capture_service,
        render_service=render_service,
    )
    app.dependency_overrides[get_portfolio_review_regenerate_service] = lambda: service
    return service


def _install_replay_service(ledger, lineage_store, capture_service, render_client, archive_client):
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=render_client,
        archive_client=archive_client,
        snapshot_store=lineage_store,
        job_ledger=ledger,
    )
    service = PortfolioReviewReplayService(
        ledger=ledger,
        capture_service=capture_service,
        render_service=render_service,
    )
    app.dependency_overrides[get_portfolio_review_replay_service] = lambda: service
    return service


def _create_archived_pdf_job(client, ledger):
    payload = _payload()
    payload["requested_output_formats"] = ["pdf"]
    response = client.post("/reports/portfolio-reviews", json=payload, headers=_headers())
    assert response.status_code == 202
    job_id = response.json()["report_job_id"]
    _run_pending_jobs(ledger)
    ready = ledger.get_job(job_id)
    assert ready.status == "data_ready"
    rendered = ledger.mark_completed(
        job_id=ready.job_id,
        actor=ready.triggered_by,
        correlation_id=ready.correlation_id,
        trace_id=ready.trace_id,
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
        artifact_sha256="sha256:artifact",
        bounded_determinism_fingerprint="fingerprint",
        runtime_engine="typst",
        runtime_engine_version="0.14.2",
        render_duration_ms=812,
    )
    ledger.mark_archiving(
        job_id=rendered.job_id,
        actor=rendered.triggered_by,
        correlation_id=rendered.correlation_id,
        trace_id=rendered.trace_id,
        archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
    )
    return ledger.mark_archived(
        job_id=rendered.job_id,
        actor=rendered.triggered_by,
        correlation_id=rendered.correlation_id,
        trace_id=rendered.trace_id,
        archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
        archive_document_id="doc_report_job_pdf",
    )


def test_portfolio_review_submission_returns_after_durable_acceptance_only(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)

    class _ForbiddenCapture:
        calls = 0

        async def capture_for_job(self, _job):
            self.calls += 1
            raise AssertionError("capture must not run on the HTTP acceptance path")

    class _ForbiddenRender:
        calls = 0

        async def render_for_job(self, _job):
            self.calls += 1
            raise AssertionError("render must not run on the HTTP acceptance path")

    capture = _ForbiddenCapture()
    render = _ForbiddenRender()
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: capture
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: render
    try:
        response = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-durable-acceptance"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "accepted"
        work_item = ledger.get_work_item_for_job(body["report_job_id"])
        assert work_item is not None
        assert work_item.status == "pending"
        assert capture.calls == 0
        assert render.calls == 0
    finally:
        _clear_overrides()


def test_portfolio_review_job_submit_status_and_cancel(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)
    try:
        submit_response = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers(),
        )

        assert submit_response.status_code == 202
        handle = submit_response.json()
        assert handle["report_request_id"].startswith("rrq_")
        assert handle["report_job_id"].startswith("rjob_")
        assert handle["status"] == "accepted"
        assert handle["status_url"] == f"/reports/jobs/{handle['report_job_id']}"
        assert handle["idempotency_key"] == _headers()["Idempotency-Key"]

        _run_pending_jobs(ledger)
        status_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}",
            headers=_headers(),
        )
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["report_job_id"] == handle["report_job_id"]
        assert status_body["report_type"] == "portfolio_review"
        assert status_body["portfolio_scope"] == {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}
        assert status_body["status"] == "data_ready"
        assert status_body["current_step"] == "data_ready"
        assert status_body["retry_eligible"] is False
        assert status_body["correlation_id"] == "corr-report-job-1"
        assert "sqlite" not in str(status_body).lower()

        list_response = client.get(
            "/reports/jobs",
            params={
                "tenantId": "tenant-sg",
                "region": "APAC",
                "status": "data_ready",
                "portfolioId": "PB_SG_GLOBAL_BAL_001",
                "asOfDate": "2026-04-22",
            },
            headers=_headers(),
        )
        assert list_response.status_code == 200
        list_body = list_response.json()
        assert list_body["count"] == 1
        assert list_body["applied_filters"]["tenant_id"] == "tenant-sg"
        assert list_body["items"][0]["report_job_id"] == handle["report_job_id"]
        assert list_body["items"][0]["idempotency_key"] == _headers()["Idempotency-Key"]

        cancel_response = client.post(
            f"/reports/jobs/{handle['report_job_id']}/cancel",
            headers={
                "X-Actor-Id": "advisor-123",
                "X-Caller-Application": "lotus-gateway",
                "X-Tenant-Id": "tenant-sg",
                "X-Region": "APAC",
                "X-Booking-Center-Code": "SG",
                "X-Correlation-ID": "corr-cancel",
            },
        )
        assert cancel_response.status_code == 200
        cancel_body = cancel_response.json()
        assert cancel_body["status"] == "cancelled"
        assert cancel_body["failure_category"] == "cancelled"
        assert cancel_body["cancel_requested"] is True
        assert cancel_body["cancelled_at"] is not None
        event_statuses = [
            event.to_status for event in ledger.list_status_events(handle["report_job_id"])
        ]
        assert event_statuses == ["accepted", "collecting_data", "data_ready", "cancelled"]

        events_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/events",
            headers=_headers(),
        )
        assert events_response.status_code == 200
        events_body = events_response.json()
        assert events_body["report_job_id"] == handle["report_job_id"]
        assert [event["to_status"] for event in events_body["events"]] == [
            "accepted",
            "collecting_data",
            "data_ready",
            "cancelled",
        ]

        diagnostics_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/diagnostics",
            headers=_headers(),
        )
        assert diagnostics_response.status_code == 200
        diagnostics_body = diagnostics_response.json()
        assert diagnostics_body["report_job_id"] == handle["report_job_id"]
        assert diagnostics_body["status"]["status"] == "cancelled"
        assert diagnostics_body["event_count"] == 4
        assert diagnostics_body["latest_event"]["to_status"] == "cancelled"
        assert diagnostics_body["snapshot"]["snapshot_id"].startswith("rsnap_")
        assert diagnostics_body["lineage"]["upstream_call_count"] == 1
        assert diagnostics_body["lineage"]["source_services"] == ["lotus-core"]
        assert diagnostics_body["operation_links"]["status_url"] == (
            f"/reports/jobs/{handle['report_job_id']}"
        )
        assert "snapshot_payload" not in str(diagnostics_body).lower()
        assert "storage_ref" not in str(diagnostics_body).lower()
        assert "response_payload" not in str(diagnostics_body).lower()
    finally:
        _clear_overrides()


def test_portfolio_review_job_persists_reviewed_proposal_narrative_package(tmp_path):
    client, _ledger, lineage_store = _client(tmp_path)
    payload = _payload()
    payload["proposal_narrative_package"] = _proposal_narrative_package()
    try:
        submit_response = client.post(
            "/reports/portfolio-reviews",
            json=payload,
            headers=_headers("portfolio-review-with-reviewed-narrative"),
        )

        assert submit_response.status_code == 202
        handle = submit_response.json()
        _run_pending_jobs(_ledger)
        snapshot = lineage_store.get_snapshot_by_job(handle["report_job_id"])
        package = snapshot.snapshot_payload["proposal_narrative_package"]
        assert package["package_status"] == "INCLUDED_REVIEWED_NARRATIVE"
        assert package["review"]["review_state"] == "APPROVED_FOR_ADVISOR_USE"
        assert package["source_lineage"]["source_narrative_hash"] == "sha256:narrative"
        assert package["sections"][0]["section_id"] == "portfolio_context"
        assert "lotus-advise" in snapshot.lineage_summary["source_services"]

        snapshot_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/snapshot",
            headers=_headers(),
        )
        assert snapshot_response.status_code == 200
        snapshot_body = snapshot_response.json()
        assert (
            snapshot_body["snapshot_payload"]["proposal_narrative_package"]["narrative_id"]
            == "pnar_001"
        )
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_unapproved_proposal_narrative_package(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    payload = _payload()
    package = _proposal_narrative_package()
    package["review"]["review_state"] = "NEEDS_REVIEW"
    payload["proposal_narrative_package"] = package
    try:
        response = client.post(
            "/reports/portfolio-reviews",
            json=payload,
            headers=_headers("portfolio-review-unapproved-narrative"),
        )

        assert response.status_code == 422
        assert "APPROVED_FOR_ADVISOR_USE" in str(response.json())
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_non_included_proposal_narrative_package(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    payload = _payload()
    package = _proposal_narrative_package()
    package["package_status"] = "REVIEW_REQUIRED"
    payload["proposal_narrative_package"] = package
    try:
        response = client.post(
            "/reports/portfolio-reviews",
            json=payload,
            headers=_headers("portfolio-review-narrative-not-included"),
        )

        assert response.status_code == 422
        assert "INCLUDED_REVIEWED_NARRATIVE" in str(response.json())
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_reviewed_package_without_source_hash(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    payload = _payload()
    package = _proposal_narrative_package()
    package["source_lineage"].pop("source_narrative_hash")
    payload["proposal_narrative_package"] = package
    try:
        response = client.post(
            "/reports/portfolio-reviews",
            json=payload,
            headers=_headers("portfolio-review-narrative-missing-source-hash"),
        )

        assert response.status_code == 422
        assert "source_narrative_hash" in str(response.json())
    finally:
        _clear_overrides()


def test_report_job_list_requires_filter(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.get("/reports/jobs", headers=_headers())

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_report_job_filters"
    finally:
        _clear_overrides()


def test_portfolio_review_job_submit_is_idempotent(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        first = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())
        second = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json() == first.json()
    finally:
        _clear_overrides()


def test_outcome_review_report_job_captures_manage_report_input_snapshot(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.post(
            "/reports/outcome-reviews",
            json=_outcome_payload(),
            headers=_headers("outcome-review-dor_001-json"),
        )

        assert response.status_code == 202
        handle = response.json()
        assert handle["status"] == "data_ready"

        status_response = client.get(f"/reports/jobs/{handle['report_job_id']}", headers=_headers())
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["report_type"] == "outcome_review"
        assert status_body["portfolio_scope"] == {
            "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
            "outcome_review_id": "dor_001",
        }

        snapshot_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/snapshot",
            headers=_headers(),
        )
        assert snapshot_response.status_code == 200
        snapshot_body = snapshot_response.json()
        assert snapshot_body["report_type"] == "outcome_review"
        assert snapshot_body["report_data_contract_version"] == "dpm_outcome_report_input.v1"
        assert snapshot_body["snapshot_payload"]["outcome_review_id"] == "dor_001"
        assert snapshot_body["snapshot_payload"]["content_hash"] == "sha256:report-input"

        lineage_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/lineage",
            headers=_headers(),
        )
        assert lineage_response.status_code == 200
        lineage_body = lineage_response.json()
        assert lineage_body["upstream_calls"][0]["service_name"] == "lotus-manage"
        assert lineage_body["upstream_calls"][0]["response_hash"] == "sha256:report-input"
    finally:
        _clear_overrides()


def test_proof_pack_report_job_captures_manage_report_input_snapshot(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.post(
            "/reports/proof-packs",
            json=_proof_pack_payload(),
            headers=_headers("proof-pack-dpp_001-json"),
        )

        assert response.status_code == 202
        handle = response.json()
        assert handle["status"] == "data_ready"

        status_response = client.get(f"/reports/jobs/{handle['report_job_id']}", headers=_headers())
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["report_type"] == "proof_pack"
        assert status_body["portfolio_scope"] == {
            "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
            "proof_pack_id": "dpp_001",
        }

        snapshot_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/snapshot",
            headers=_headers(),
        )
        assert snapshot_response.status_code == 200
        snapshot_body = snapshot_response.json()
        assert snapshot_body["report_type"] == "proof_pack"
        assert snapshot_body["report_data_contract_version"] == "dpm_proof_pack_report_input.v1"
        assert snapshot_body["snapshot_payload"]["proof_pack_id"] == "dpp_001"
        assert snapshot_body["snapshot_payload"]["content_hash"] == "sha256:report-input"

        lineage_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/lineage",
            headers=_headers(),
        )
        assert lineage_response.status_code == 200
        lineage_body = lineage_response.json()
        assert lineage_body["upstream_calls"][0]["service_name"] == "lotus-manage"
        assert lineage_body["upstream_calls"][0]["response_hash"] == "sha256:report-input"
    finally:
        _clear_overrides()


def test_wave_report_job_captures_manage_report_input_snapshot(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.post(
            "/reports/rebalance-waves",
            json=_wave_payload(),
            headers=_headers("wave-dwv_001-json"),
        )

        assert response.status_code == 202
        handle = response.json()
        assert handle["status"] == "data_ready"

        status_response = client.get(f"/reports/jobs/{handle['report_job_id']}", headers=_headers())
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["report_type"] == "rebalance_wave"
        assert status_body["portfolio_scope"] == {
            "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
            "wave_id": "dwv_001",
            "proof_pack_ids": ["dpp_001"],
        }

        snapshot_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/snapshot",
            headers=_headers(),
        )
        assert snapshot_response.status_code == 200
        snapshot_body = snapshot_response.json()
        assert snapshot_body["report_type"] == "rebalance_wave"
        assert snapshot_body["report_data_contract_version"] == "dpm_wave_report_input.v1"
        assert snapshot_body["snapshot_payload"]["wave_id"] == "dwv_001"
        assert snapshot_body["snapshot_payload"]["content_hash"] == "sha256:wave-report-input"

        lineage_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/lineage",
            headers=_headers(),
        )
        assert lineage_response.status_code == 200
        lineage_body = lineage_response.json()
        assert lineage_body["upstream_calls"][0]["service_name"] == "lotus-manage"
        assert lineage_body["upstream_calls"][0]["response_hash"] == "sha256:wave-report-input"
        assert lineage_body["upstream_calls"][0]["endpoint"].endswith(
            "/waves/{wave_id}/report-input"
        )
    finally:
        _clear_overrides()


def test_dpm_report_jobs_reject_missing_required_source_evidence(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        outcome_payload = deepcopy(_outcome_payload())
        outcome_payload["outcome_report_input"].pop("content_hash")
        proof_payload = deepcopy(_proof_pack_payload())
        proof_payload["proof_pack_report_input"].pop("proof_pack_content_hash")
        wave_payload = deepcopy(_wave_payload())
        wave_payload["wave_report_input"]["source_refs"] = []

        outcome = client.post(
            "/reports/outcome-reviews",
            json=outcome_payload,
            headers=_headers("outcome-review-missing-source-evidence"),
        )
        proof = client.post(
            "/reports/proof-packs",
            json=proof_payload,
            headers=_headers("proof-pack-missing-source-evidence"),
        )
        wave = client.post(
            "/reports/rebalance-waves",
            json=wave_payload,
            headers=_headers("wave-missing-source-evidence"),
        )

        assert outcome.status_code == 422
        assert proof.status_code == 422
        assert wave.status_code == 422
        assert "content_hash" in str(outcome.json()["detail"])
        assert "proof_pack_content_hash" in str(proof.json()["detail"])
        assert "source_refs" in str(wave.json()["detail"])
    finally:
        _clear_overrides()


def test_proof_pack_report_job_does_not_recapture_data_ready_replay(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    try:
        first = client.post(
            "/reports/proof-packs",
            json=_proof_pack_payload(),
            headers=_headers("proof-pack-dpp_001-replay"),
        )
        second = client.post(
            "/reports/proof-packs",
            json=_proof_pack_payload(),
            headers=_headers("proof-pack-dpp_001-replay"),
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["status"] == "data_ready"
        assert second.json()["status"] == "data_ready"
        assert second.json()["report_job_id"] == first.json()["report_job_id"]
        assert capture_service.calls == 1
    finally:
        _clear_overrides()


def test_outcome_review_report_job_requires_idempotency_key(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    headers = {key: value for key, value in _headers().items() if key != "Idempotency-Key"}
    try:
        response = client.post(
            "/reports/outcome-reviews",
            json=_outcome_payload(),
            headers=headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == {
            "code": "missing_idempotency_key",
            "message": "Idempotency-Key is required.",
        }
    finally:
        _clear_overrides()


def test_outcome_review_report_job_rejects_idempotency_conflict(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    conflict_payload = _outcome_payload()
    conflict_payload["outcome_report_input"] = {
        **conflict_payload["outcome_report_input"],
        "content_hash": "sha256:changed-report-input",
    }
    try:
        first = client.post(
            "/reports/outcome-reviews",
            json=_outcome_payload(),
            headers=_headers("outcome-review-conflict"),
        )
        second = client.post(
            "/reports/outcome-reviews",
            json=conflict_payload,
            headers=_headers("outcome-review-conflict"),
        )

        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["detail"] == {
            "code": "idempotency_conflict",
            "message": "Idempotency-Key was reused with a different report request.",
        }
    finally:
        _clear_overrides()


def test_proof_pack_report_job_translates_ledger_missing_idempotency_error(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)

    def _raise_missing_key(**_kwargs):
        raise MissingIdempotencyKeyError("missing_idempotency_key")

    ledger.create_proof_pack_report_job = _raise_missing_key
    try:
        response = client.post(
            "/reports/proof-packs",
            json=_proof_pack_payload(),
            headers=_headers("proof-pack-ledger-missing-key"),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == {
            "code": "missing_idempotency_key",
            "message": "Idempotency-Key is required.",
        }
    finally:
        _clear_overrides()


def test_proof_pack_report_job_rejects_idempotency_conflict(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    conflict_payload = _proof_pack_payload()
    conflict_payload["proof_pack_report_input"] = {
        **conflict_payload["proof_pack_report_input"],
        "content_hash": "sha256:changed-report-input",
    }
    try:
        first = client.post(
            "/reports/proof-packs",
            json=_proof_pack_payload(),
            headers=_headers("proof-pack-conflict"),
        )
        second = client.post(
            "/reports/proof-packs",
            json=conflict_payload,
            headers=_headers("proof-pack-conflict"),
        )

        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["detail"] == {
            "code": "idempotency_conflict",
            "message": "Idempotency-Key was reused with a different report request.",
        }
    finally:
        _clear_overrides()


def test_outcome_review_report_job_invokes_render_for_pdf_request(tmp_path):
    render_service = _CountingRenderService()
    client, _ledger, _lineage_store = _client(tmp_path)
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        render_service
    )
    payload = _outcome_payload()
    payload["requested_output_formats"] = ["pdf"]
    try:
        response = client.post(
            "/reports/outcome-reviews",
            json=payload,
            headers=_headers("outcome-review-dor_001-pdf"),
        )

        assert response.status_code == 202
        assert response.json()["status"] == "data_ready"
        assert render_service.calls == 1
    finally:
        _clear_overrides()


def test_proof_pack_report_job_invokes_render_for_pdf_request(tmp_path):
    render_service = _CountingRenderService()
    client, _ledger, _lineage_store = _client(tmp_path)
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        render_service
    )
    payload = _proof_pack_payload()
    payload["requested_output_formats"] = ["pdf"]
    try:
        response = client.post(
            "/reports/proof-packs",
            json=payload,
            headers=_headers("proof-pack-dpp_001-pdf"),
        )

        assert response.status_code == 202
        assert response.json()["status"] == "data_ready"
        assert render_service.calls == 1
    finally:
        _clear_overrides()


def test_wave_report_job_invokes_render_for_pdf_request(tmp_path):
    render_service = _CountingRenderService()
    client, _ledger, _lineage_store = _client(tmp_path)
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        render_service
    )
    payload = _wave_payload()
    payload["requested_output_formats"] = ["pdf"]
    try:
        response = client.post(
            "/reports/rebalance-waves",
            json=payload,
            headers=_headers("wave-dwv_001-pdf"),
        )

        assert response.status_code == 202
        assert response.json()["status"] == "data_ready"
        assert render_service.calls == 1
    finally:
        _clear_overrides()


def test_portfolio_review_job_submit_can_complete_pdf_render_flow(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)
    try:
        payload = _payload()
        payload["requested_output_formats"] = ["pdf"]

        class _CompletingRenderService:
            async def render_for_job(self, job):
                rendered = ledger.mark_completed(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    render_job_id=f"rdr_{job.job_id}_pdf",
                    output_format="pdf",
                    template_id="portfolio-review",
                    template_version="v1",
                    artifact_sha256="sha256:artifact",
                    bounded_determinism_fingerprint="fingerprint",
                    runtime_engine="typst",
                    runtime_engine_version="0.14.2",
                    render_duration_ms=812,
                )
                ledger.mark_archiving(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    archive_request_id=f"arch_rdr_{job.job_id}_pdf",
                )
                return ledger.mark_archived(
                    job_id=rendered.job_id,
                    actor=rendered.triggered_by,
                    correlation_id=rendered.correlation_id,
                    trace_id=rendered.trace_id,
                    archive_request_id=f"arch_rdr_{job.job_id}_pdf",
                    archive_document_id="doc_report_job_pdf",
                )

        app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
            _CompletingRenderService()
        )

        response = client.post("/reports/portfolio-reviews", json=payload, headers=_headers())

        assert response.status_code == 202
        handle = response.json()
        assert handle["status"] == "accepted"
        _run_pending_jobs(ledger)

        status_response = client.get(f"/reports/jobs/{handle['report_job_id']}", headers=_headers())
        assert status_response.status_code == 200
        body = status_response.json()
        assert body["status"] == "archived"
        assert body["render"]["render_job_id"] == f"rdr_{handle['report_job_id']}_pdf"
        assert body["render"]["artifact_sha256"] == "sha256:artifact"
        assert body["archive"]["archive_request_id"] == f"arch_rdr_{handle['report_job_id']}_pdf"
        assert body["archive"]["document_id"] == "doc_report_job_pdf"
    finally:
        _clear_overrides()


def test_portfolio_review_job_does_not_recapture_collecting_data_replay(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )

    try:
        first = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())
        _run_pending_jobs(ledger)
        second = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())

        assert first.status_code == 202
        assert second.status_code == 202
        assert capture_service.calls == 1
        assert second.json()["report_job_id"] == first.json()["report_job_id"]
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_missing_idempotency_key(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.post("/reports/portfolio-reviews", json=_payload(), headers={})

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_idempotency_key"
    finally:
        _clear_overrides()


def test_portfolio_review_job_translates_ledger_missing_idempotency_error(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)

    def _raise_missing_key(**_kwargs):
        raise MissingIdempotencyKeyError("missing_idempotency_key")

    ledger.submit_portfolio_review_job = _raise_missing_key
    try:
        response = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-ledger-missing-key"),
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_idempotency_key"
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_missing_caller_context(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        headers = {"Idempotency-Key": "portfolio-review-missing-context"}
        response = client.post("/reports/portfolio-reviews", json=_payload(), headers=headers)

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "missing_caller_context"
        assert detail["missing_headers"] == [
            "X-Actor-Id",
            "X-Caller-Application",
            "X-Tenant-Id",
            "X-Region",
        ]
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_idempotency_conflict(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        first = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())
        changed_payload = _payload()
        changed_payload["reporting_currency"] = "CHF"
        conflict = client.post(
            "/reports/portfolio-reviews",
            json=changed_payload,
            headers=_headers(),
        )

        assert first.status_code == 202
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    finally:
        _clear_overrides()


def test_report_job_unknown_and_duplicate_cancel_are_product_safe(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        unknown = client.get("/reports/jobs/rjob_missing", headers=_headers())
        assert unknown.status_code == 404
        assert unknown.json()["detail"]["code"] == "report_job_not_found"
        unknown_diagnostics = client.get(
            "/reports/jobs/rjob_missing/diagnostics", headers=_headers()
        )
        assert unknown_diagnostics.status_code == 404
        assert unknown_diagnostics.json()["detail"]["code"] == "report_job_not_found"

        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-cancel-repeat"),
        ).json()
        first_cancel = client.post(
            f"/reports/jobs/{handle['report_job_id']}/cancel",
            headers=_headers("portfolio-review-cancel-repeat"),
        )
        duplicate_cancel = client.post(
            f"/reports/jobs/{handle['report_job_id']}/cancel",
            headers=_headers("portfolio-review-cancel-repeat"),
        )

        assert first_cancel.status_code == 200
        assert duplicate_cancel.status_code == 409
        assert duplicate_cancel.json()["detail"]["code"] == "report_job_cannot_be_cancelled"
        assert "traceback" not in str(duplicate_cancel.json()).lower()
    finally:
        _clear_overrides()


def test_report_job_diagnostics_requires_caller_context(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.get("/reports/jobs/rjob_missing/diagnostics")

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_caller_context"
    finally:
        _clear_overrides()


def test_report_job_diagnostics_reports_missing_snapshot_without_payload_leak(tmp_path):
    client, _ledger, lineage_store = _client(tmp_path)

    class _NoSnapshotCaptureService:
        async def capture_for_job(self, job):
            return job

    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        _NoSnapshotCaptureService()
    )
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-no-snapshot"),
        ).json()

        response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/diagnostics",
            headers=_headers("portfolio-review-no-snapshot"),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["snapshot"] is None
        assert body["lineage"] is None
        assert body["operation_links"]["snapshot_url"] is None
        assert body["operation_links"]["lineage_url"] is None
        assert body["diagnostic_flags"] == ["snapshot_not_captured"]
        assert "snapshot_payload" not in str(body).lower()
        assert lineage_store.list_upstream_calls("missing") == []
    finally:
        _clear_overrides()


def test_report_job_diagnostics_surfaces_cloned_snapshot_evidence_pointers(tmp_path):
    """A replay-cloned snapshot has zero upstream-call rows of its own; the
    diagnostics must say the evidence is cloned and name the source snapshot
    instead of presenting a bare zero-call summary that reads as missing
    evidence."""

    client, ledger, lineage_store = _client(tmp_path)
    handle = client.post(
        "/reports/portfolio-reviews",
        json=_payload(),
        headers=_headers("portfolio-review-cloned-lineage"),
    ).json()
    job = ledger.get_job(handle["report_job_id"])
    lineage_store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload={"readiness": {"status": "ready"}},
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={
                "source_services": ["lotus-core", "lotus-performance"],
                "call_count": 0,
                "upstream_evidence": "cloned_from_source_snapshot",
                "source_call_count": 4,
                "cloned_from_report_job_id": "rjob_source",
                "cloned_from_snapshot_id": "rsnap_source",
            },
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )

    response = client.get(
        f"/reports/jobs/{job.job_id}/diagnostics",
        headers=_headers("portfolio-review-cloned-lineage"),
    )

    assert response.status_code == 200
    lineage = response.json()["lineage"]
    assert lineage["upstream_call_count"] == 0
    assert lineage["source_services"] == ["lotus-core", "lotus-performance"]
    assert lineage["upstream_evidence"] == "cloned_from_source_snapshot"
    assert lineage["evidence_source_snapshot_id"] == "rsnap_source"
    assert lineage["evidence_source_report_job_id"] == "rjob_source"
    assert lineage["source_upstream_call_count"] == 4


def test_report_job_diagnostics_surfaces_replay_fingerprint_outcome_flags(tmp_path):
    """Non-matched fingerprint comparisons must be visible as diagnostic
    flags so operators do not have to query the event history (issue #202)."""

    client, ledger, _lineage_store = _client(tmp_path)
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("fingerprint-flag-job"),
        ).json()
        job_id = handle["report_job_id"]
        job = ledger.get_job(job_id)
        ledger.append_job_event(
            job_id=job_id,
            event_type="job_replay_fingerprint_compared",
            message="Replay fingerprint comparison against rjob_src: diverged.",
            event_payload={
                "outcome": "diverged",
                "reason": "same_runtime_fingerprint_mismatch",
                "source_report_job_id": "rjob_src",
            },
            event_idempotency_key=f"job_replay_fingerprint_compared:{job_id}",
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )

        response = client.get(
            f"/reports/jobs/{job_id}/diagnostics",
            headers=_headers("fingerprint-flag-job"),
        )

        assert response.status_code == 200
        assert "replay_fingerprint_diverged" in response.json()["diagnostic_flags"]
    finally:
        _clear_overrides()


def test_report_job_diagnostics_reports_failed_retryable_job_flags(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-diagnostics-failed"),
        ).json()
        job_id = handle["report_job_id"]
        _run_pending_jobs(ledger)
        failed = ledger.mark_failed(
            job_id=job_id,
            actor="advisor-123",
            correlation_id="corr-diagnostics-failed",
            trace_id="trace-diagnostics-failed",
            failure_category="upstream_data_failed",
            failure_message="Snapshot capture timed out.",
            retry_eligible=True,
        )

        response = client.get(
            f"/reports/jobs/{failed.job_id}/diagnostics",
            headers=_headers("portfolio-review-diagnostics-failed"),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"]["status"] == "failed"
        assert body["diagnostic_flags"] == ["job_failed", "retry_eligible"]
    finally:
        _clear_overrides()


def test_report_job_diagnostics_reports_unarchived_render_flag(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-diagnostics-unarchived"),
        ).json()
        job_id = handle["report_job_id"]
        _run_pending_jobs(ledger)
        rendered = ledger.mark_completed(
            job_id=job_id,
            actor="advisor-123",
            correlation_id="corr-diagnostics-unarchived",
            trace_id="trace-diagnostics-unarchived",
            render_job_id=f"rdr_{job_id}_pdf",
            output_format="pdf",
            template_id="portfolio-review",
            template_version="v1",
            artifact_sha256="sha256:artifact",
            bounded_determinism_fingerprint="fingerprint",
            runtime_engine="typst",
            runtime_engine_version="0.14.2",
            render_duration_ms=812,
        )

        response = client.get(
            f"/reports/jobs/{rendered.job_id}/diagnostics",
            headers=_headers("portfolio-review-diagnostics-unarchived"),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"]["status"] == "completed"
        assert body["diagnostic_flags"] == ["archive_not_completed"]
    finally:
        _clear_overrides()


def test_report_job_portfolio_memory_events_are_source_owned_and_support_safe(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)
    try:
        handle = client.post(
            "/reports/proof-packs",
            json=_proof_pack_payload(),
            headers=_headers("proof-pack-memory-events"),
        ).json()
        job_id = handle["report_job_id"]
        completed = ledger.mark_completed(
            job_id=job_id,
            actor="advisor-123",
            correlation_id="corr-memory-events",
            trace_id="trace-memory-events",
            render_job_id=f"rdr_{job_id}_pdf",
            output_format="pdf",
            template_id="proof-pack",
            template_version="v1",
            artifact_sha256="sha256:artifact-proof-pack",
            bounded_determinism_fingerprint="typst-0.14.2:proof",
            runtime_engine="typst",
            runtime_engine_version="0.14.2",
            render_duration_ms=812,
        )
        ledger.mark_archiving(
            job_id=completed.job_id,
            actor="advisor-123",
            correlation_id="corr-memory-events",
            trace_id="trace-memory-events",
            archive_request_id=f"arch_{job_id}_pdf",
        )
        archived = ledger.mark_archived(
            job_id=completed.job_id,
            actor="advisor-123",
            correlation_id="corr-memory-events",
            trace_id="trace-memory-events",
            archive_request_id=f"arch_{job_id}_pdf",
            archive_document_id=f"doc_{job_id}",
        )

        response = client.get(
            f"/reports/jobs/{archived.job_id}/portfolio-memory-events",
            headers=_headers("proof-pack-memory-events"),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["report_job_id"] == archived.job_id
        assert body["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
        assert body["report_type"] == "proof_pack"
        assert body["supportability_state"] == "READY"
        assert body["governance_policy"]["redaction_policy"] == "NO_RAW_PAYLOADS"
        assert body["events"][-1]["event_type"] == "REPORT_ARCHIVED"
        assert body["events"][-1]["artifact_refs"] == [
            {
                "artifact_system": "lotus-render",
                "artifact_type": "RENDERED_REPORT_ARTIFACT",
                "artifact_id": f"rdr_{job_id}_pdf",
                "content_hash": "sha256:artifact-proof-pack",
            },
            {
                "artifact_system": "lotus-archive",
                "artifact_type": "ARCHIVED_REPORT_DOCUMENT",
                "artifact_id": f"doc_{job_id}",
                "content_hash": "sha256:artifact-proof-pack",
            },
        ]
        assert "REPORT_INPUT_SNAPSHOT" in {
            ref["source_type"] for ref in body["events"][-1]["source_refs"]
        }
        assert "snapshot_payload" not in str(body).lower()
        assert "snapshot_storage_ref" not in str(body).lower()
    finally:
        _clear_overrides()


def test_report_job_diagnostics_translates_lineage_store_unavailable(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)

    class _UnavailableLineageStore:
        def get_snapshot_by_job(self, _report_job_id):
            raise RuntimeError("database connection failed")

    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-lineage-unavailable"),
        ).json()
        app.dependency_overrides[get_report_lineage_store] = lambda: _UnavailableLineageStore()

        response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/diagnostics",
            headers=_headers("portfolio-review-lineage-unavailable"),
        )

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "report_lineage_store_unavailable"
        assert "database" not in str(response.json()).lower()
    finally:
        _clear_overrides()


def test_report_job_events_and_cancel_translate_unknown_job(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)

    def _raise_not_found(*_args, **_kwargs):
        raise ReportJobNotFoundError("report_job_not_found")

    ledger.get_job = _raise_not_found
    ledger.cancel_job = _raise_not_found
    try:
        events = client.get("/reports/jobs/rjob_missing/events", headers=_headers())
        cancel = client.post("/reports/jobs/rjob_missing/cancel", headers=_headers())

        assert events.status_code == 404
        assert events.json()["detail"]["code"] == "report_job_not_found"
        assert cancel.status_code == 404
        assert cancel.json()["detail"]["code"] == "report_job_not_found"
    finally:
        _clear_overrides()


def test_report_job_openapi_examples_are_full_and_do_not_leak_rfc_names():
    client = TestClient(app)
    schema = client.get("/openapi.json").json()

    submit_post = schema["paths"]["/reports/portfolio-reviews"]["post"]
    request_example = submit_post["requestBody"]["content"]["application/json"]["example"]
    response_example = submit_post["responses"]["202"]["content"]["application/json"]["example"]
    status_get = schema["paths"]["/reports/jobs/{job_id}"]["get"]
    status_example = status_get["responses"]["200"]["content"]["application/json"]["example"]
    list_get = schema["paths"]["/reports/jobs"]["get"]
    list_example = list_get["responses"]["200"]["content"]["application/json"]["example"]
    events_get = schema["paths"]["/reports/jobs/{job_id}/events"]["get"]
    events_example = events_get["responses"]["200"]["content"]["application/json"]["example"]
    memory_get = schema["paths"]["/reports/jobs/{job_id}/portfolio-memory-events"]["get"]
    memory_example = memory_get["responses"]["200"]["content"]["application/json"]["example"]
    diagnostics_get = schema["paths"]["/reports/jobs/{job_id}/diagnostics"]["get"]
    diagnostics_example = diagnostics_get["responses"]["200"]["content"]["application/json"][
        "example"
    ]
    regenerate_post = schema["paths"]["/reports/jobs/{job_id}/regenerate"]["post"]
    regenerate_example = regenerate_post["responses"]["202"]["content"]["application/json"][
        "example"
    ]
    replay_post = schema["paths"]["/reports/jobs/{job_id}/replay"]["post"]
    replay_example = replay_post["responses"]["202"]["content"]["application/json"]["example"]

    assert request_example["portfolio_scope"]["portfolio_ids"] == ["PB_SG_GLOBAL_BAL_001"]
    assert (
        request_example["proposal_narrative_package"]["review"]["review_state"]
        == "APPROVED_FOR_ADVISOR_USE"
    )
    assert (
        request_example["proposal_narrative_package"]["source_lineage"]["source_narrative_hash"]
        == "sha256:narrative"
    )
    assert response_example["report_job_id"].startswith("rjob_")
    assert status_example["status"] == "archived"
    assert status_example["render"]["render_job_id"].startswith("rdr_")
    assert status_example["archive"]["document_id"].startswith("doc_")
    assert list_example["items"][0]["report_job_id"].startswith("rjob_")
    assert events_example["events"][0]["event_type"] == "job_accepted"
    assert memory_example["events"][0]["event_type"] == "REPORT_JOB_ACCEPTED"
    assert memory_example["governance_policy"]["redaction_policy"] == "NO_RAW_PAYLOADS"
    assert diagnostics_example["lineage"]["upstream_call_count"] == 3
    assert diagnostics_example["operation_links"]["lineage_url"].endswith("/lineage")
    assert "snapshot_payload" not in str(diagnostics_example)
    assert regenerate_example["source_report_job_id"].startswith("rjob_")
    assert regenerate_example["regenerated_report_job_id"].startswith("rjob_")
    assert regenerate_example["new_snapshot_id"] != regenerate_example["previous_snapshot_id"]
    assert (
        regenerate_example["new_archive_document_id"]
        != regenerate_example["previous_archive_document_id"]
    )
    assert regenerate_example["archive_consequence"] == "replacement"
    assert replay_example["source_report_job_id"].startswith("rjob_")
    assert replay_example["replayed_report_job_id"].startswith("rjob_")
    assert replay_example["replayed_report_job_id"] != replay_example["source_report_job_id"]
    assert replay_example["source_failure_category"] == "upstream_data_failed"
    assert "Report Jobs" in list_get["tags"]
    assert "Report Jobs" in memory_get["tags"]
    assert "Report Jobs" in diagnostics_get["tags"]
    assert "Report Jobs" in regenerate_post["tags"]
    assert "Report Jobs" in replay_post["tags"]
    assert "what" in list_get["description"].lower() or "returns" in list_get["description"].lower()
    assert (
        "when" in list_get["description"].lower()
        or "use this endpoint" in list_get["description"].lower()
    )
    assert "use this endpoint" in diagnostics_get["description"].lower()
    assert "downstream portfolio-memory consumers" in memory_get["description"]
    assert "upstream" in regenerate_post["description"].lower()
    assert "rerender" in regenerate_post["description"].lower()
    assert "failed" in replay_post["description"].lower()
    assert "rerender" in replay_post["description"].lower()
    assert "RFC-" not in str(request_example)
    assert "RFC-" not in str(response_example)
    assert "RFC-" not in str(status_example)
    assert "RFC-" not in str(list_example)
    assert "RFC-" not in str(events_example)
    assert "RFC-" not in str(memory_example)
    assert "RFC-" not in str(diagnostics_example)
    assert "RFC-" not in str(regenerate_example)
    assert "RFC-" not in str(replay_example)
    for schema_name in [
        "ReportJobHandleResponse",
        "ReportJobStatusResponse",
        "ReportJobDiagnosticsResponse",
        "ReportJobSnapshotDiagnostics",
        "ReportJobLineageDiagnostics",
        "ReportJobOperationLinks",
        "ReportJobListResponse",
        "ReportJobListItem",
        "ReportJobListFilters",
        "ReportJobStatusEventsResponse",
        "ReportPortfolioMemoryArtifactRef",
        "ReportPortfolioMemoryEvent",
        "ReportPortfolioMemoryEventsResponse",
        "ReportPortfolioMemoryGovernancePolicy",
        "ReportPortfolioMemorySourceRef",
        "ReportJobRegenerateRequest",
        "ReportJobRegenerateResponse",
        "ReportJobReplayRequest",
        "ReportJobReplayResponse",
        "ReportStatusEvent",
        "ApiErrorResponse",
        "ApiErrorDetail",
    ]:
        properties = schema["components"]["schemas"][schema_name]["properties"]
        for property_contract in properties.values():
            assert property_contract.get("description")


def test_dpm_report_job_openapi_uses_bounded_input_schemas():
    client = TestClient(app)
    schema = client.get("/openapi.json").json()
    schemas = schema["components"]["schemas"]

    assert schemas["OutcomeReviewReportJobRequest"]["properties"]["outcome_report_input"][
        "$ref"
    ].endswith("/DpmOutcomeReportInput")
    assert schemas["ProofPackReportJobRequest"]["properties"]["proof_pack_report_input"][
        "$ref"
    ].endswith("/DpmProofPackReportInput")
    assert schemas["WaveReportJobRequest"]["properties"]["wave_report_input"]["$ref"].endswith(
        "/DpmWaveReportInput"
    )
    for schema_name, hash_field in [
        ("DpmOutcomeReportInput", "outcome_review_content_hash"),
        ("DpmProofPackReportInput", "proof_pack_content_hash"),
        ("DpmWaveReportInput", "wave_content_hash"),
    ]:
        dpm_schema = schemas[schema_name]
        assert hash_field in dpm_schema["required"]
        assert "content_hash" in dpm_schema["required"]
        assert "evidence_ref" in dpm_schema["required"]
        assert "redaction_policy" in dpm_schema["required"]
        assert "retention_policy" in dpm_schema["required"]


def test_report_job_snapshot_and_lineage_endpoints_are_support_safe(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers(),
        ).json()
        _run_pending_jobs(_ledger)

        snapshot_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/snapshot",
            headers=_headers(),
        )
        assert snapshot_response.status_code == 200
        snapshot_body = snapshot_response.json()
        assert snapshot_body["report_job_id"] == handle["report_job_id"]
        assert snapshot_body["supportability_status"] == "complete"

        lineage_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/lineage",
            headers=_headers(),
        )
        assert lineage_response.status_code == 200
        lineage_body = lineage_response.json()
        assert lineage_body["snapshot"]["report_job_id"] == handle["report_job_id"]
        assert lineage_body["upstream_calls"][0]["service_name"] == "lotus-core"
        assert "response_payload" not in str(lineage_body).lower()

        snapshot_id = snapshot_body["snapshot_id"]
        snapshot_by_id = client.get(f"/reports/snapshots/{snapshot_id}", headers=_headers())
        assert snapshot_by_id.status_code == 200
        assert snapshot_by_id.json()["snapshot_id"] == snapshot_id

        snapshot_lineage = client.get(
            f"/reports/snapshots/{snapshot_id}/lineage",
            headers=_headers(),
        )
        assert snapshot_lineage.status_code == 200
        assert snapshot_lineage.json()["snapshot"]["snapshot_id"] == snapshot_id
    finally:
        _clear_overrides()


def test_job_identity_fence_answers_not_found_on_every_job_scoped_route(tmp_path):
    """Issue #203: a caller from another tenant, region, or booking centre
    gets the unknown-id 404 on every job-scoped read and command, with zero
    side effects - existence is never leaked and state never changes."""

    client, ledger, lineage_store = _client(tmp_path)
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("job-fence-source"),
        ).json()
        job_id = handle["report_job_id"]
        job = ledger.get_job(job_id)
        lineage_store.create_snapshot(
            ReportInputSnapshotCreateRequest(
                report_job_id=job_id,
                report_type=job.report_type,
                report_data_contract_version="v1",
                portfolio_scope=job.portfolio_scope,
                as_of_date=job.as_of_date,
                snapshot_payload={"readiness": {"status": "ready"}},
                snapshot_storage_ref=None,
                supportability_status="complete",
                completeness_status="complete",
                lineage_summary={"source_services": ["lotus-core"], "call_count": 0},
                captured_at=datetime.now(UTC),
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        )
        snapshot_id = lineage_store.get_snapshot_by_job(job_id).snapshot_id

        reads = [
            ("GET", f"/reports/jobs/{job_id}", "report_job_not_found"),
            ("GET", f"/reports/jobs/{job_id}/diagnostics", "report_job_not_found"),
            ("GET", f"/reports/jobs/{job_id}/events", "report_job_not_found"),
            (
                "GET",
                f"/reports/jobs/{job_id}/portfolio-memory-events",
                "report_job_not_found",
            ),
            ("GET", f"/reports/jobs/{job_id}/snapshot", "report_job_not_found"),
            ("GET", f"/reports/jobs/{job_id}/lineage", "report_job_not_found"),
            ("GET", f"/reports/snapshots/{snapshot_id}", "report_snapshot_not_found"),
            (
                "GET",
                f"/reports/snapshots/{snapshot_id}/lineage",
                "report_snapshot_not_found",
            ),
        ]
        commands = [
            ("POST", f"/reports/jobs/{job_id}/cancel", None, "report_job_not_found"),
            (
                "POST",
                f"/reports/jobs/{job_id}/rerender",
                {"reason": "cross tenant"},
                "report_job_not_found",
            ),
            (
                "POST",
                f"/reports/jobs/{job_id}/regenerate",
                {"reason": "cross tenant"},
                "report_job_not_found",
            ),
            (
                "POST",
                f"/reports/jobs/{job_id}/replay",
                {"reason": "cross tenant"},
                "report_job_not_found",
            ),
        ]
        foreign_axes = [
            {"X-Tenant-Id": "tenant-other"},
            {"X-Region": "EMEA"},
            {"X-Booking-Center-Code": "HK"},
        ]
        for axis in foreign_axes:
            foreign_headers = {**_headers(f"fence-{list(axis)[0]}"), **axis}
            for method, url, code in reads:
                response = client.request(method, url, headers=foreign_headers)
                assert response.status_code == 404, (axis, url, response.text)
                assert response.json()["detail"]["code"] == code, (axis, url)
            for method, url, body, code in commands:
                response = client.request(method, url, json=body, headers=foreign_headers)
                assert response.status_code == 404, (axis, url, response.text)
                assert response.json()["detail"]["code"] == code, (axis, url)

        # Zero side effects: state unchanged, no attempts, no relationships,
        # no derived jobs, and the rightful caller still sees the job.
        unchanged = ledger.get_job(job_id)
        assert unchanged.status == job.status
        assert ledger.list_rerender_attempts(job_id) == []
        assert ledger.list_job_relationships(job_id) == []
        rightful = client.get(f"/reports/jobs/{job_id}", headers=_headers())
        assert rightful.status_code == 200
    finally:
        _clear_overrides()


def test_report_job_snapshot_endpoints_translate_missing_snapshot_rows(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        missing_job_snapshot = client.get("/reports/jobs/rjob_missing/snapshot", headers=_headers())
        missing_job_lineage = client.get("/reports/jobs/rjob_missing/lineage", headers=_headers())
        missing_snapshot = client.get("/reports/snapshots/rsnap_missing", headers=_headers())
        missing_snapshot_lineage = client.get(
            "/reports/snapshots/rsnap_missing/lineage",
            headers=_headers(),
        )

        assert missing_job_snapshot.status_code == 404
        assert missing_job_snapshot.json()["detail"]["code"] == "report_job_not_found"
        assert missing_job_lineage.status_code == 404
        assert missing_job_lineage.json()["detail"]["code"] == "report_job_not_found"
        assert missing_snapshot.status_code == 404
        assert missing_snapshot.json()["detail"]["code"] == "report_snapshot_not_found"
        assert missing_snapshot_lineage.status_code == 404
        assert missing_snapshot_lineage.json()["detail"]["code"] == "report_snapshot_not_found"
    finally:
        _clear_overrides()


def test_report_job_rerender_uses_existing_snapshot_and_archives_correction(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    try:
        job = _create_archived_pdf_job(client, ledger)
        snapshot = lineage_store.get_snapshot_by_job(job.job_id)
        capture_calls_after_create = capture_service.calls

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-template-correction"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "archived"
        assert body["snapshot_id"] == snapshot.snapshot_id
        assert body["snapshot_hash"] == snapshot.snapshot_hash
        assert body["previous_render_job_id"] == f"rdr_{job.job_id}_pdf"
        assert body["previous_archive_document_id"] == "doc_report_job_pdf"
        assert body["render"]["render_job_id"].startswith("rdr_rrnd_")
        assert body["render"]["render_job_id"] != job.render_job_id
        assert body["archive"]["document_id"] == "doc_report_job_pdf_correction"
        assert body["archive_consequence"] == "correction"
        assert capture_service.calls == capture_calls_after_create

        assert len(render_client.payloads) == 1
        assert render_client.payloads[0]["snapshot_id"] == snapshot.snapshot_id
        assert "snapshot_hash" not in render_client.payloads[0]
        assert "render_attempt_id" not in render_client.payloads[0]
        assert len(archive_client.payloads) == 1
        metadata = archive_client.payloads[0]["metadata"]
        assert metadata["snapshot_id"] == snapshot.snapshot_id
        assert metadata["snapshot_hash"] == snapshot.snapshot_hash
        assert metadata["render_attempt_id"] == body["rerender_attempt_id"]
        assert metadata["supersedes_render_job_id"] == f"rdr_{job.job_id}_pdf"
        assert metadata["supersedes_archive_document_id"] == "doc_report_job_pdf"
        assert metadata["archive_consequence"] == "correction"

        events = ledger.list_status_events(job.job_id)
        assert [event.event_type for event in events][-2:] == [
            "job_rerender_requested",
            "job_rerender_archived",
        ]

        diagnostics = client.get(
            f"/reports/jobs/{job.job_id}/diagnostics",
            headers=_headers(),
        )
        assert diagnostics.status_code == 200
        attempts = diagnostics.json()["rerender_attempts"]
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt["rerender_attempt_id"] == body["rerender_attempt_id"]
        assert attempt["status"] == "archived"
        assert attempt["snapshot_id"] == snapshot.snapshot_id
        assert attempt["snapshot_hash"] == snapshot.snapshot_hash
        assert attempt["previous_render_job_id"] == f"rdr_{job.job_id}_pdf"
        assert attempt["previous_archive_document_id"] == "doc_report_job_pdf"
        assert attempt["archive_consequence"] == "correction"
        assert attempt["render"]["render_job_id"] == body["render"]["render_job_id"]
        assert attempt["archive"]["document_id"] == "doc_report_job_pdf_correction"
        assert "idempotency_key" not in attempt
        assert "correlation_id" not in attempt
        assert "trace_id" not in attempt
    finally:
        _clear_overrides()


def test_report_job_rerender_is_idempotent(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)
        headers = _headers(f"rerender-{job.job_id}-same-key")

        first = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=headers,
        )
        second = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=headers,
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json() == first.json()
        assert len(render_client.payloads) == 1
        assert len(archive_client.payloads) == 1
    finally:
        _clear_overrides()


def test_report_job_rerender_rejects_non_archived_job(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    _install_rerender_service(
        ledger,
        lineage_store,
        _RerenderRenderClient(),
        _RerenderArchiveClient(),
    )
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers(),
        ).json()

        response = client.post(
            f"/reports/jobs/{handle['report_job_id']}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{handle['report_job_id']}-invalid"),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "report_job_cannot_be_rerendered"
    finally:
        _clear_overrides()


def test_report_job_rerender_reports_missing_snapshot(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    _install_rerender_service(
        ledger,
        lineage_store,
        _RerenderRenderClient(),
        _RerenderArchiveClient(),
    )
    try:
        job = _create_archived_pdf_job(client, ledger)
        missing_snapshot_store = ReportInputSnapshotStore(tmp_path / "empty-lineage.sqlite3")
        _install_rerender_service(
            ledger,
            missing_snapshot_store,
            _RerenderRenderClient(),
            _RerenderArchiveClient(),
        )

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-missing-snapshot"),
        )

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "report_job_not_found"
    finally:
        _clear_overrides()


def test_report_job_rerender_records_render_validation_failure(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient(
        status_code=422,
        payload={
            "detail": {
                "code": "render_package_invalid",
                "message": "Render package failed contract validation.",
            }
        },
    )
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-render-validation"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "failed"
        assert body["failure_category"] == "render_validation_failed"
        assert body["retry_eligible"] is False
        assert body["archive"] is None
        assert len(archive_client.payloads) == 0
        assert ledger.list_status_events(job.job_id)[-1].event_type == "job_rerender_failed"

        diagnostics = client.get(
            f"/reports/jobs/{job.job_id}/diagnostics",
            headers=_headers(),
        )
        assert diagnostics.status_code == 200
        attempts = diagnostics.json()["rerender_attempts"]
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt["rerender_attempt_id"] == body["rerender_attempt_id"]
        assert attempt["status"] == "failed"
        assert attempt["failure_category"] == "render_validation_failed"
        assert attempt["failure_message"] == body["failure_message"]
        assert attempt["retry_eligible"] is False
        assert attempt["archive"] is None
        assert "idempotency_key" not in attempt
        assert "correlation_id" not in attempt
        assert "trace_id" not in attempt
    finally:
        _clear_overrides()


def test_report_job_rerender_records_retryable_render_execution_failure(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient(
        status_code=503,
        payload={"failure_message": "Render worker unavailable."},
    )
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-render-execution"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "failed"
        assert body["failure_category"] == "render_execution_failed"
        assert body["failure_message"] == "Render worker unavailable."
        assert body["retry_eligible"] is True
        assert body["archive"] is None
        assert len(archive_client.payloads) == 0
    finally:
        _clear_overrides()


def test_report_job_rerender_records_non_retryable_render_conflict(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient(
        status_code=409,
        payload={"detail": "Render job already exists."},
    )
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-render-conflict"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "failed"
        assert body["failure_category"] == "render_conflict"
        assert body["failure_message"] == "Render job already exists."
        assert body["retry_eligible"] is False
        assert len(archive_client.payloads) == 0
    finally:
        _clear_overrides()


def test_report_job_rerender_records_missing_artifact_archive_validation_failure(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient(
        payload={
            "status": "rendered",
            "render_job_id": "rdr_missing_artifact",
            "artifact_sha256": "sha256:rerender-artifact",
            "bounded_determinism_fingerprint": "fingerprint-rerender",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 731,
        },
    )
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-missing-artifact"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "failed"
        # A "rendered" response without bytes is a replay of a completed render,
        # not an archive defect: it stays retry-eligible because a new rerender
        # attempt regenerates the artifact from the retained snapshot.
        assert body["failure_category"] == "render_artifact_unrecoverable"
        assert body["retry_eligible"] is True
        assert body["archive"] is None
        assert len(archive_client.payloads) == 0
        assert ledger.list_status_events(job.job_id)[-1].event_type == "job_rerender_failed"
    finally:
        _clear_overrides()


def test_report_job_rerender_records_archive_failure(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient(
        status_code=503,
        payload={
            "detail": {
                "code": "archive_storage_unavailable",
                "message": "Archive storage is unavailable.",
            }
        },
    )
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-archive-failure"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "failed"
        assert body["failure_category"] == "archive_storage_failed"
        assert body["retry_eligible"] is True
        assert body["archive"]["archive_request_id"].startswith("arch_rdr_rrnd_")
        assert ledger.list_status_events(job.job_id)[-1].event_type == "job_rerender_failed"
    finally:
        _clear_overrides()


def test_report_job_regenerate_creates_new_snapshot_lineage_and_replacement_archive(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient(
        payload={"document_id": "doc_report_job_pdf_replacement"}
    )
    _install_regenerate_service(
        ledger,
        lineage_store,
        capture_service,
        render_client,
        archive_client,
    )
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    try:
        source = _create_archived_pdf_job(client, ledger)
        previous_snapshot = lineage_store.get_snapshot_by_job(source.job_id)

        response = client.post(
            f"/reports/jobs/{source.job_id}/regenerate",
            json={"reason": "Certified upstream position correction."},
            headers=_headers(f"regenerate-{source.job_id}-upstream-correction"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "archived"
        assert body["source_report_job_id"] == source.job_id
        assert body["regenerated_report_job_id"] != source.job_id
        assert body["previous_snapshot_id"] == previous_snapshot.snapshot_id
        assert body["new_snapshot_id"] != previous_snapshot.snapshot_id
        assert body["previous_snapshot_hash"] == previous_snapshot.snapshot_hash
        assert body["new_snapshot_hash"] != previous_snapshot.snapshot_hash
        assert body["previous_archive_document_id"] == "doc_report_job_pdf"
        assert body["new_archive_document_id"] == "doc_report_job_pdf_replacement"
        assert body["archive_consequence"] == "replacement"

        new_calls = lineage_store.list_upstream_calls_by_job(body["regenerated_report_job_id"])
        assert [call.service_name for call in new_calls] == ["lotus-core"]
        metadata = archive_client.payloads[0]["metadata"]
        assert metadata["supersedes_render_job_id"] == f"rdr_{source.job_id}_pdf"
        assert metadata["supersedes_archive_document_id"] == "doc_report_job_pdf"
        assert metadata["archive_consequence"] == "replacement"
        assert [event.event_type for event in ledger.list_status_events(source.job_id)][-2:] == [
            "job_regenerate_requested",
            "job_regenerate_archived",
        ]
        regenerate_events = ledger.list_status_events(source.job_id)[-2:]
        assert (
            regenerate_events[0].event_payload["regenerated_job_id"]
            == body["regenerated_report_job_id"]
        )
        assert regenerate_events[1].event_family == "regenerate_lifecycle"
        assert regenerate_events[1].event_payload["archive_document_id"] == (
            "doc_report_job_pdf_replacement"
        )
        source_diagnostics = client.get(
            f"/reports/jobs/{source.job_id}/diagnostics",
            headers=_headers(f"diagnostics-{source.job_id}-regenerate-source"),
        )
        derived_diagnostics = client.get(
            f"/reports/jobs/{body['regenerated_report_job_id']}/diagnostics",
            headers=_headers(f"diagnostics-{body['regenerated_report_job_id']}-regenerate-derived"),
        )
        assert source_diagnostics.status_code == 200
        assert derived_diagnostics.status_code == 200
        source_relationship = source_diagnostics.json()["relationships"][0]
        derived_relationship = derived_diagnostics.json()["relationships"][0]
        assert source_relationship == derived_relationship
        assert source_relationship["relationship_type"] == "regenerate_replacement"
        assert source_relationship["source_report_job_id"] == source.job_id
        assert source_relationship["derived_report_job_id"] == body["regenerated_report_job_id"]
        assert source_relationship["source_status"] == "archived"
        assert source_relationship["derived_status"] == "archived"
        assert source_relationship["archive_consequence"] == "replacement"
        assert source_relationship["previous_archive_document_id"] == "doc_report_job_pdf"
        assert source_relationship["new_archive_document_id"] == "doc_report_job_pdf_replacement"
    finally:
        _clear_overrides()


def test_report_job_regenerate_is_idempotent(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient(
        payload={"document_id": "doc_report_job_pdf_replacement"}
    )
    _install_regenerate_service(
        ledger,
        lineage_store,
        capture_service,
        render_client,
        archive_client,
    )
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    try:
        source = _create_archived_pdf_job(client, ledger)
        headers = _headers(f"regenerate-{source.job_id}-same-key")
        calls_after_source = capture_service.calls

        first = client.post(
            f"/reports/jobs/{source.job_id}/regenerate",
            json={"reason": "Certified upstream position correction."},
            headers=headers,
        )
        second = client.post(
            f"/reports/jobs/{source.job_id}/regenerate",
            json={"reason": "Certified upstream position correction."},
            headers=headers,
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json() == first.json()
        assert capture_service.calls == calls_after_source + 1
        assert len(render_client.payloads) == 1
        assert len(archive_client.payloads) == 1
        assert [
            event.event_type
            for event in ledger.list_status_events(source.job_id)
            if event.event_type == "job_regenerate_archived"
        ] == ["job_regenerate_archived"]
    finally:
        _clear_overrides()


def test_report_job_regenerate_rejects_non_archived_job(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    _install_regenerate_service(
        ledger,
        lineage_store,
        capture_service,
        _RerenderRenderClient(),
        _RerenderArchiveClient(),
    )
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-regenerate-invalid"),
        ).json()

        response = client.post(
            f"/reports/jobs/{handle['report_job_id']}/regenerate",
            json={"reason": "Certified upstream position correction."},
            headers=_headers(f"regenerate-{handle['report_job_id']}-invalid"),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "report_job_cannot_be_regenerated"
    finally:
        _clear_overrides()


def test_report_job_regenerate_records_upstream_failure_without_render(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)

    class _FailingCaptureService:
        async def capture_for_job(self, job):
            ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
            return ledger.mark_failed(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                failure_category="upstream_data_failed",
                failure_message="Upstream report-data capture failed.",
                retry_eligible=True,
            )

    capture_service = _FailingCaptureService()
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient()
    _install_regenerate_service(
        ledger,
        lineage_store,
        capture_service,
        render_client,
        archive_client,
    )
    try:
        source = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{source.job_id}/regenerate",
            json={"reason": "Certified upstream position correction."},
            headers=_headers(f"regenerate-{source.job_id}-upstream-failure"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "failed"
        assert body["failure_category"] == "upstream_data_failed"
        assert body["retry_eligible"] is True
        assert body["new_snapshot_id"] is None
        assert len(render_client.payloads) == 0
        assert len(archive_client.payloads) == 0
        diagnostics = client.get(
            f"/reports/jobs/{body['regenerated_report_job_id']}/diagnostics",
            headers=_headers(f"diagnostics-{body['regenerated_report_job_id']}-regenerate-failed"),
        )
        assert diagnostics.status_code == 200
        relationship = diagnostics.json()["relationships"][0]
        assert relationship["relationship_type"] == "regenerate_replacement"
        assert relationship["source_report_job_id"] == source.job_id
        assert relationship["derived_report_job_id"] == body["regenerated_report_job_id"]
        assert relationship["source_status"] == "archived"
        assert relationship["derived_status"] == "failed"
        assert relationship["derived_failure_category"] == "upstream_data_failed"
        assert relationship["previous_archive_document_id"] == "doc_report_job_pdf"
        assert relationship["new_archive_document_id"] is None
    finally:
        _clear_overrides()


def test_report_job_regenerate_allows_partial_snapshot_lineage(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)

    class _PartialCaptureService(_FakeCaptureService):
        async def capture_for_job(self, job):
            self.calls += 1
            ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
            snapshot = lineage_store.create_snapshot(
                ReportInputSnapshotCreateRequest(
                    report_job_id=job.job_id,
                    report_type=job.report_type,
                    report_data_contract_version="v1",
                    portfolio_scope=job.portfolio_scope,
                    as_of_date=job.as_of_date,
                    snapshot_payload={
                        "report_id": f"portfolio-review:{job.job_id}",
                        "portfolio_id": job.portfolio_scope["portfolio_ids"][0],
                        "as_of_date": job.as_of_date.isoformat(),
                        "capture_sequence": self.calls,
                        "readiness": {"status": "partial"},
                    },
                    snapshot_storage_ref=None,
                    supportability_status="partial",
                    completeness_status="partial",
                    lineage_summary={
                        "source_services": ["lotus-core"],
                        "call_count": 1,
                        "supportability_status": "partial",
                        "partial_call_count": 1,
                        "unavailable_call_count": 0,
                        "not_supported_call_count": 0,
                        "redacted_call_count": 0,
                    },
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            )
            lineage_store.create_upstream_calls(
                snapshot_id=snapshot.snapshot_id,
                calls=[
                    ReportUpstreamCallCreateRequest(
                        service_name="lotus-core",
                        endpoint="/reporting/portfolio-summary/query",
                        method="POST",
                        contract_version="v1",
                        request_hash="sha256:req-partial",
                        response_hash="sha256:resp-partial",
                        response_ref=None,
                        status_code=206,
                        latency_ms=251,
                        supportability_status="partial",
                        completeness_status="partial",
                        failure_category="none",
                        failure_message=None,
                        captured_at=datetime.now(UTC),
                        correlation_id=job.correlation_id,
                        trace_id=job.trace_id,
                    )
                ],
            )
            return ledger.mark_data_ready(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )

    capture_service = _PartialCaptureService(ledger, lineage_store)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient(payload={"document_id": "doc_partial_replacement"})
    _install_regenerate_service(
        ledger,
        lineage_store,
        capture_service,
        render_client,
        archive_client,
    )
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    try:
        source = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{source.job_id}/regenerate",
            json={"reason": "Refresh with partially supported upstream evidence."},
            headers=_headers(f"regenerate-{source.job_id}-partial"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "archived"
        assert body["new_snapshot_id"]
        assert body["new_archive_document_id"] == "doc_partial_replacement"
        snapshot = lineage_store.get_snapshot_by_job(body["regenerated_report_job_id"])
        assert snapshot.supportability_status == "partial"
        assert lineage_store.list_upstream_calls(snapshot.snapshot_id)[0].supportability_status == (
            "partial"
        )
    finally:
        _clear_overrides()


def test_report_job_replay_creates_new_job_and_is_idempotent(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient(payload={"document_id": "doc_report_job_pdf_replay"})
    _install_replay_service(
        ledger,
        lineage_store,
        capture_service,
        render_client,
        archive_client,
    )
    try:
        payload = _payload()
        payload["requested_output_formats"] = ["pdf"]
        handle = client.post(
            "/reports/portfolio-reviews",
            json=payload,
            headers=_headers("portfolio-review-replay-source"),
        ).json()
        failed_at = datetime.now(UTC) + timedelta(seconds=1)
        work = ledger.claim_work_items(
            worker_id="report-worker-replay-proof",
            limit=1,
            lease_seconds=30,
            retry_policy=ReportJobWorkRetryPolicy(max_attempts=1),
            now=failed_at,
        )[0]
        ledger.fail_work_item(
            work_item_id=work.work_item_id,
            lease_token=work.lease_token or "",
            error_category="report_job_worker_execution_failed",
            error_summary="Upstream capture remained unavailable.",
            retry_policy=ReportJobWorkRetryPolicy(max_attempts=1),
            now=failed_at,
        )
        source = ledger.get_job(handle["report_job_id"])
        assert source.status == "failed"
        assert source.retry_eligible is True
        headers = _headers(f"replay-{source.job_id}-same-key")

        first = client.post(
            f"/reports/jobs/{source.job_id}/replay",
            json={"reason": "Retry after upstream service recovered."},
            headers=headers,
        )
        second = client.post(
            f"/reports/jobs/{source.job_id}/replay",
            json={"reason": "Retry after upstream service recovered."},
            headers=headers,
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json() == first.json()
        body = first.json()
        assert body["source_report_job_id"] == source.job_id
        assert body["replayed_report_job_id"] != source.job_id
        assert body["status"] == "archived"
        assert body["source_failure_category"] == "operator_intervention_required"
        assert body["archive"]["document_id"] == "doc_report_job_pdf_replay"
        assert len(render_client.payloads) == 1
        assert len(archive_client.payloads) == 1
        assert [
            event.event_type
            for event in ledger.list_status_events(source.job_id)
            if event.event_type == "job_replay_completed"
        ] == ["job_replay_completed"]
        replay_event = [
            event
            for event in ledger.list_status_events(source.job_id)
            if event.event_type == "job_replay_completed"
        ][0]
        assert replay_event.event_family == "replay_lifecycle"
        assert replay_event.event_payload["replayed_job_id"] == body["replayed_report_job_id"]
        assert replay_event.event_payload["replayed_status"] == "archived"
        source_diagnostics = client.get(
            f"/reports/jobs/{source.job_id}/diagnostics",
            headers=_headers(f"diagnostics-{source.job_id}-replay-source"),
        )
        replayed_diagnostics = client.get(
            f"/reports/jobs/{body['replayed_report_job_id']}/diagnostics",
            headers=_headers(f"diagnostics-{body['replayed_report_job_id']}-replay-derived"),
        )
        assert source_diagnostics.status_code == 200
        assert replayed_diagnostics.status_code == 200
        source_relationship = source_diagnostics.json()["relationships"][0]
        replayed_relationship = replayed_diagnostics.json()["relationships"][0]
        assert source_relationship == replayed_relationship
        assert source_relationship["relationship_type"] == "failed_work_replay"
        assert source_relationship["source_report_job_id"] == source.job_id
        assert source_relationship["derived_report_job_id"] == body["replayed_report_job_id"]
        assert source_relationship["source_status"] == "failed"
        assert source_relationship["derived_status"] == "archived"
        assert source_relationship["source_failure_category"] == "operator_intervention_required"
        assert source_relationship["new_archive_document_id"] == "doc_report_job_pdf_replay"
    finally:
        _clear_overrides()


def test_report_job_replay_rejects_non_retryable_and_archived_jobs(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    _install_replay_service(
        ledger,
        lineage_store,
        capture_service,
        _RerenderRenderClient(),
        _RerenderArchiveClient(),
    )
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-replay-nonretryable"),
        ).json()
        failed = ledger.mark_failed(
            job_id=handle["report_job_id"],
            actor="advisor-123",
            correlation_id="corr-report-job-1",
            trace_id="trace-report-job-1",
            failure_category="validation_failed",
            failure_message="Non retryable validation failure.",
            retry_eligible=False,
        )
        archived = _create_archived_pdf_job(client, ledger)

        nonretry_response = client.post(
            f"/reports/jobs/{failed.job_id}/replay",
            json={"reason": "Should be rejected."},
            headers=_headers(f"replay-{failed.job_id}-invalid"),
        )
        archived_response = client.post(
            f"/reports/jobs/{archived.job_id}/replay",
            json={"reason": "Should be rejected."},
            headers=_headers(f"replay-{archived.job_id}-invalid"),
        )

        assert nonretry_response.status_code == 409
        assert archived_response.status_code == 409
        assert nonretry_response.json()["detail"]["code"] == "report_job_cannot_be_replayed"
        assert archived_response.json()["detail"]["code"] == "report_job_cannot_be_replayed"
    finally:
        _clear_overrides()


def test_report_job_replay_error_mappings(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    _install_replay_service(
        ledger,
        lineage_store,
        _FakeCaptureService(ledger, lineage_store),
        _RerenderRenderClient(),
        _RerenderArchiveClient(),
    )
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-replay-errors"),
        ).json()
        failed = ledger.mark_failed(
            job_id=handle["report_job_id"],
            actor="advisor-123",
            correlation_id="corr-report-job-1",
            trace_id="trace-report-job-1",
            failure_category="upstream_data_failed",
            failure_message="Upstream timeout.",
            retry_eligible=True,
        )

        missing_key = client.post(
            f"/reports/jobs/{failed.job_id}/replay",
            json={"reason": "Missing key."},
            headers={key: value for key, value in _headers().items() if key != "Idempotency-Key"},
        )
        not_found = client.post(
            "/reports/jobs/rjob_missing/replay",
            json={"reason": "Missing job."},
            headers=_headers("replay-missing-job"),
        )

        assert missing_key.status_code == 400
        assert missing_key.json()["detail"]["code"] == "missing_idempotency_key"
        assert not_found.status_code == 404
        assert not_found.json()["detail"]["code"] == "report_job_not_found"
    finally:
        _clear_overrides()


def test_report_job_replay_conflict_mapping() -> None:
    class _ConflictReplayService:
        async def replay_job(self, **_kwargs):
            raise InvalidReportJobTransitionError("report_job_cannot_be_replayed")

    app.dependency_overrides[get_portfolio_review_replay_service] = lambda: _ConflictReplayService()
    client = TestClient(app)
    try:
        response = client.post(
            "/reports/jobs/rjob_conflict/replay",
            json={"reason": "Unsupported replay state."},
            headers=_headers("replay-conflict"),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "report_job_cannot_be_replayed"
    finally:
        _clear_overrides()
