from datetime import UTC, datetime

import pytest

from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render import service as render_service
from app.reporting_render.service import (
    PortfolioReviewRenderOrchestrationService,
    _build_render_package,
    _holding_observation,
    _optional_decimal,
    _optional_int,
    _optional_str,
    _performance_observation,
    _risk_observation,
)


class _RenderClientSuccess:
    async def submit_render_package(self, payload, correlation_id=None):
        assert correlation_id == "corr-render"
        assert payload["report_data"]["client_name"] == "Alex Tan"
        return 201, {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": "portfolio-review",
            "template_version": "v1",
            "artifact_sha256": "sha256:artifact",
            "bounded_determinism_fingerprint": "fingerprint",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 812,
        }


class _RenderClientFailure:
    async def submit_render_package(self, payload, correlation_id=None):
        return 422, {
            "detail": {
                "code": "render_package_invalid",
                "message": "Template payload was invalid.",
            }
        }


class _RenderClientConflict:
    async def submit_render_package(self, payload, correlation_id=None):
        return 409, {
            "detail": {
                "code": "render_job_conflict",
                "message": "Render job already exists.",
            }
        }


class _RenderClientServerError:
    async def submit_render_package(self, payload, correlation_id=None):
        return 503, {"failure_message": "lotus-render unavailable"}


def _job_request(**overrides):
    payload = {
        "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "as_of_date": "2026-04-22",
        "requested_output_formats": ["pdf"],
        "reporting_currency": "USD",
        "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
    }
    payload.update(overrides)
    return PortfolioReviewJobRequest.model_validate(payload)


def _caller():
    return ReportCallerContext.model_validate(
        {
            "triggered_by": "advisor-123",
            "caller_application": "lotus-gateway",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "booking_center_code": "SG",
            "role": "advisor",
            "correlation_id": "corr-render",
            "trace_id": "trace-render",
        }
    )


def _seed_data_ready_job(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-render",
    )
    ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload={
                "readiness": {"status": "ready"},
                "reportingCurrency": "USD",
                "clientProfile": {"identity": {"client_name": "Alex Tan"}},
                "overview": {"total_market_value": 15234567.89, "currency": "USD"},
                "keyFigures": {
                    "performance": {
                        "largest_positive_contributor": {
                            "security_name": "Global Equity Sleeve",
                            "ytd_contribution_pct": 3.5,
                        }
                    },
                    "risk": {"ytd_volatility_pct": 12.0, "ytd_beta": 0.82},
                    "holdings": {"position_count": 12},
                },
            },
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-core"], "call_count": 1},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    return ledger, store, ready


@pytest.mark.asyncio
async def test_render_orchestration_marks_job_completed(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    completed = await service.render_for_job(ready)

    assert completed.status == "completed"
    assert completed.render_job_id == f"rdr_{ready.job_id}_pdf"
    assert completed.render_artifact_sha256 == "sha256:artifact"
    assert completed.render_runtime_engine == "typst"
    assert completed.render_duration_ms == 812
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
    ]


@pytest.mark.asyncio
async def test_render_orchestration_marks_validation_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientFailure(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "render_validation_failed"
    assert failed.retry_eligible is False
    assert failed.render_job_id == f"rdr_{ready.job_id}_pdf"


@pytest.mark.asyncio
async def test_render_orchestration_skips_non_pdf_requests(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(requested_output_formats=["json"]),
        caller_context=_caller(),
        idempotency_key="idem-no-pdf",
    )
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        snapshot_store=object(),
        job_ledger=ledger,
    )

    returned = await service.render_for_job(job)

    assert returned.job_id == job.job_id
    assert returned.status == "accepted"


@pytest.mark.asyncio
async def test_render_orchestration_marks_conflict_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientConflict(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "render_conflict"
    assert failed.retry_eligible is False


@pytest.mark.asyncio
async def test_render_orchestration_marks_retryable_execution_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientServerError(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "render_execution_failed"
    assert failed.failure_message == "lotus-render unavailable"
    assert failed.retry_eligible is True


def test_build_render_package_uses_fallback_values_for_sparse_snapshot(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-sparse",
    )

    payload = _build_render_package(
        job=job,
        snapshot={"overview": {"currency": "SGD", "total_market_value": "1000.50"}},
        render_job_id="rdr-sparse",
    )

    assert payload["snapshot_id"] == f"snapshot-for-{job.job_id}"
    assert payload["report_data"]["client_name"] == "Client"
    assert payload["report_data"]["currency"] == "SGD"
    assert payload["report_data"]["total_value"] == "1000.50"
    assert payload["report_data"]["review_observations"] == [
        "Portfolio review was rendered from the governed lotus-report snapshot."
    ]


def test_render_service_helpers_cover_fallback_branches(monkeypatch):
    assert _performance_observation({"benchmark_comparison_status": "not_available"}) == (
        "Benchmark comparison status is not_available in the governed report snapshot."
    )
    assert _risk_observation({}) is None
    assert _holding_observation({"position_count": "7"}) == (
        "The report includes 7 sourced portfolio positions."
    )
    assert _optional_str("  trimmed  ") == "trimmed"
    assert _optional_str("   ") is None
    assert _optional_decimal(True) is None
    assert _optional_decimal(5) is not None
    assert _optional_decimal("bad-decimal") is None
    assert _optional_int(True) == 1
    assert _optional_int("bad-int") is None

    class _SentinelClient:
        pass

    sentinel_client = _SentinelClient()
    sentinel_store = object()
    sentinel_ledger = object()

    monkeypatch.setattr(render_service, "RenderClient", lambda **kwargs: sentinel_client)
    monkeypatch.setattr(render_service, "get_report_input_snapshot_store", lambda: sentinel_store)
    monkeypatch.setattr(render_service, "get_report_job_ledger", lambda: sentinel_ledger)
    render_service.get_portfolio_review_render_orchestration_service.cache_clear()
    try:
        service = render_service.get_portfolio_review_render_orchestration_service()
    finally:
        render_service.get_portfolio_review_render_orchestration_service.cache_clear()

    assert service._render_client is sentinel_client
    assert service._snapshot_store is sentinel_store
    assert service._job_ledger is sentinel_ledger
