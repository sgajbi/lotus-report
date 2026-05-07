from datetime import UTC, datetime

import pytest

from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import (
    OutcomeReviewReportJobRequest,
    PortfolioReviewJobRequest,
    ProofPackReportJobRequest,
    ReportCallerContext,
)
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render import service as render_service
from app.reporting_render.package_builder import (
    _allocation_bucket_rows,
    _build_render_package,
    _holding_observation,
    _optional_decimal,
    _optional_int,
    _optional_str,
    _performance_history,
    _performance_observation,
    _risk_observation,
    _transactions,
)
from app.reporting_render.service import (
    PortfolioReviewRenderOrchestrationService,
    _archive_failure_message,
    _archive_failure_posture,
    _date_text,
)


class _RenderClientSuccess:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        assert correlation_id == "corr-render"
        assert trace_id == "trace-render"
        assert payload["report_data"]["client_name"] == "Alex Tan"
        assert payload["report_data"]["performance_periods"][0]["period"] == "YTD"
        assert payload["report_data"]["performance_summary_table"][0]["label"] == "Year-to-date"
        assert payload["report_data"]["performance_monthly_history"][0]["period"] == "2026-01"
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
            "artifact_base64": "JVBERi0xLjQKJQ==",
        }


class _RenderClientSuccessWithoutArtifact:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
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
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 422, {
            "detail": {
                "code": "render_package_invalid",
                "message": "Template payload was invalid.",
            }
        }


class _RenderClientConflict:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 409, {
            "detail": {
                "code": "render_job_conflict",
                "message": "Render job already exists.",
            }
        }


class _RenderClientServerError:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 503, {"failure_message": "lotus-render unavailable"}


class _ArchiveClientSuccess:
    def __init__(self):
        self.payloads = []

    async def archive_document(self, payload, **kwargs):
        self.payloads.append((payload, kwargs))
        assert kwargs["actor_id"] == "advisor-123"
        assert kwargs["tenant_id"] == "tenant-sg"
        assert kwargs["region"] == "APAC"
        assert payload["metadata"]["report_job_id"].startswith("rjob_")
        assert payload["metadata"]["snapshot_id"].startswith("rsnap_")
        assert payload["metadata"]["render_job_id"].startswith("rdr_")
        assert payload["metadata"]["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
        assert payload["metadata"]["portfolio_scope"] == (
            '{"portfolio_ids":["PB_SG_GLOBAL_BAL_001"]}'
        )
        assert payload["metadata"]["classification"] == "confidential"
        assert payload["content_base64"] == "JVBERi0xLjQKJQ=="
        return 201, {"document_id": "doc_archived"}


class _ArchiveClientValidationFailure:
    async def archive_document(self, payload, **kwargs):
        return 422, {
            "detail": {
                "code": "archive_metadata_invalid",
                "message": "Archive metadata was invalid.",
            }
        }


class _ArchiveClientStorageFailure:
    async def archive_document(self, payload, **kwargs):
        return 503, {"detail": "archive storage unavailable"}


class _ArchiveClientExecutionFailure:
    async def archive_document(self, payload, **kwargs):
        return 500, {"detail": "archive execution failed"}


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


def _proof_pack_report_input() -> dict:
    return {
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
        "evidence_ref": {
            "source_system": "lotus-manage",
            "source_type": "DPM_PROOF_PACK_REPORT_INPUT",
            "source_id": "dpp_001:dpm_proof_pack_report_input",
            "content_hash": "sha256:report-input",
        },
        "content_hash": "sha256:report-input",
    }


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
                "reviewPeriod": {"label": "YTD"},
                "clientProfile": {
                    "identity": {
                        "client_name": "Alex Tan",
                        "advisor_id": "RM_SG_001",
                        "booking_center_code": "Singapore",
                    },
                    "mandate_profile": {"risk_exposure": "balanced"},
                },
                "overview": {"total_market_value": 15234567.89, "currency": "USD"},
                "performance": {
                    "summary": {
                        "YTD": {
                            "net_cumulative_return": 4.1,
                            "benchmark_cumulative_return": 3.4,
                            "benchmark_relative_return": 0.7,
                        },
                        "1Y": {
                            "net_cumulative_return": 7.08,
                            "net_annualized_return": 7.08,
                        },
                    },
                    "monthly_history": [
                        {
                            "period": "2026-01",
                            "period_start": "2026-01-01",
                            "period_end": "2026-01-31",
                            "end_market_value": 5_214_639.0,
                            "inflows": 5_841_778.0,
                            "outflows": -5_841_749.0,
                            "performance_value": -87_158.0,
                            "cumulative_performance_value": -87_158.0,
                            "twr_pct": -1.64,
                            "cumulative_twr_pct": -1.64,
                        }
                    ],
                    "annual_history": [
                        {
                            "period": "2025",
                            "period_start": "2025-01-01",
                            "period_end": "2025-12-31",
                            "end_market_value": 5_296_856.0,
                            "inflows": 80_000.0,
                            "outflows": -20_000.0,
                            "performance_value": 226_856.0,
                            "cumulative_performance_value": 226_856.0,
                            "twr_pct": 4.5,
                            "cumulative_twr_pct": 12.0,
                        }
                    ],
                },
                "riskAnalytics": {
                    "summary": {
                        "YTD": {
                            "volatility": 12.0,
                            "beta": 0.82,
                            "tracking_error": 4.0,
                            "information_ratio": 0.72,
                            "value_at_risk": -2.0,
                        }
                    }
                },
                "holdings": {
                    "holdingsByAssetClass": {
                        "Equity": [
                            {
                                "security_id": "EQ-1",
                                "instrument_name": "Global Equity Sleeve",
                                "isin": "US0000000001",
                                "quantity": 2200.0,
                                "position_date": "2026-04-22",
                                "product_type": "Fund",
                                "sector": "Technology",
                                "country_of_risk": "United States",
                                "rating": "A",
                                "liquidity_tier": "High",
                                "held_since_date": "2024-01-15",
                                "market_price": 102.35,
                                "cost_basis_reporting_currency": 500000.0,
                                "weight": 60.0,
                                "market_value_reporting_currency": 600000.0,
                                "unrealized_pnl_reporting_currency": 100000.0,
                                "unrealized_pnl_pct": 20.0,
                                "ytd_contribution_pct": 3.5,
                                "ytd_average_weight_pct": 55.0,
                                "ytd_total_return_pct": 8.4,
                                "currency": "USD",
                            }
                        ]
                    }
                },
                "transactions": {
                    "transactionsByCategory": {
                        "Trading": [
                            {
                                "transaction_id": "TXN-1",
                                "transaction_date": "2026-04-18",
                                "transaction_type": "SELL",
                                "instrument_id": "INST-1",
                                "security_id": "EQ-1",
                                "transaction_category": "Trading",
                                "display_label": "Sell",
                                "cash_leg": False,
                                "asset_class": "Equity",
                                "amount_reporting_currency": 25000.0,
                                "gross_transaction_amount_reporting_currency": 25000.0,
                                "realized_pnl_reporting_currency": 1250.0,
                                "realized_pnl_local": 1250.0,
                                "net_interest_amount_reporting_currency": 0.0,
                                "withholding_tax_amount_reporting_currency": 0.0,
                                "income_or_tax_reporting_currency": 0.0,
                            }
                        ]
                    },
                    "transactionCount": 1,
                },
                "reviewObservations": [
                    {
                        "observation_id": "obs-1",
                        "summary": (
                            "Equity sleeve remained the main performance driver "
                            "through the review period."
                        ),
                    }
                ],
                "evidence": {
                    "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
                    "trust_metadata": {
                        "completeness_status": "complete",
                        "data_quality_status": "quality_passed",
                    },
                },
                "keyFigures": {
                    "client_profile": {
                        "objective": (
                            "Long-term real wealth growth with controlled income and liquidity."
                        )
                    },
                    "portfolio_value": {
                        "invested_market_value_reporting_currency": 14984567.89,
                        "cash_balance_reporting_currency": 250000.0,
                        "cash_weight_pct": 1.64,
                    },
                    "allocation": {
                        "name": "Equity",
                        "weight_pct": 60.0,
                        "market_value_reporting_currency": 600000.0,
                        "position_count": 3,
                    },
                    "performance": {
                        "largest_positive_contributor": {
                            "security_name": "Global Equity Sleeve",
                            "total_contribution_pct": 3.5,
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
    archive_client = _ArchiveClientSuccess()
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        archive_client=archive_client,
        snapshot_store=store,
        job_ledger=ledger,
    )

    completed = await service.render_for_job(ready)

    assert completed.status == "archived"
    assert completed.render_job_id == f"rdr_{ready.job_id}_pdf"
    assert completed.render_artifact_sha256 == "sha256:artifact"
    assert completed.render_runtime_engine == "typst"
    assert completed.render_duration_ms == 812
    assert completed.archive_request_id == f"arch_rdr_{ready.job_id}_pdf"
    assert completed.archive_document_id == "doc_archived"
    assert completed.archive_completed_at is not None
    assert archive_client.payloads
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
        "archiving",
        "archived",
    ]


@pytest.mark.asyncio
async def test_render_orchestration_marks_validation_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientFailure(),
        archive_client=_ArchiveClientSuccess(),
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
        archive_client=_ArchiveClientSuccess(),
        snapshot_store=object(),
        job_ledger=ledger,
    )

    returned = await service.render_for_job(job)

    assert returned.job_id == job.job_id
    assert returned.status == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "rendering", "accepted"])
async def test_render_orchestration_skips_jobs_that_are_not_data_ready_for_pdf(
    tmp_path,
    status,
):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    if status == "completed":
        job = ledger.mark_completed(
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
    elif status == "failed":
        job = ledger.mark_failed(
            job_id=ready.job_id,
            actor=ready.triggered_by,
            correlation_id=ready.correlation_id,
            trace_id=ready.trace_id,
            failure_category="render_execution_failed",
            failure_message="Render worker unavailable.",
            retry_eligible=True,
        )
    elif status == "cancelled":
        fresh = ledger.create_portfolio_review_job(
            request=_job_request(),
            caller_context=_caller(),
            idempotency_key="idem-render-cancelled-skip",
        )
        job = ledger.cancel_job(
            job_id=fresh.job_id,
            actor=fresh.triggered_by,
            correlation_id=fresh.correlation_id,
            trace_id=fresh.trace_id,
        )
    elif status == "rendering":
        job = ledger.mark_rendering(
            job_id=ready.job_id,
            actor=ready.triggered_by,
            correlation_id=ready.correlation_id,
            trace_id=ready.trace_id,
            render_job_id=f"rdr_{ready.job_id}_pdf",
            output_format="pdf",
            template_id="portfolio-review",
            template_version="v1",
        )
    else:
        job = ledger.create_portfolio_review_job(
            request=_job_request(),
            caller_context=_caller(),
            idempotency_key="idem-render-accepted-skip",
        )

    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        archive_client=_ArchiveClientSuccess(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    returned = await service.render_for_job(job)

    assert returned == job


@pytest.mark.asyncio
async def test_render_orchestration_marks_conflict_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientConflict(),
        archive_client=_ArchiveClientSuccess(),
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
        archive_client=_ArchiveClientSuccess(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "render_execution_failed"
    assert failed.failure_message == "lotus-render unavailable"
    assert failed.retry_eligible is True


@pytest.mark.asyncio
async def test_render_orchestration_marks_missing_artifact_as_archive_validation_failure(
    tmp_path,
):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccessWithoutArtifact(),
        archive_client=_ArchiveClientSuccess(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_validation_failed"
    assert failed.retry_eligible is False
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
        "failed",
    ]


@pytest.mark.asyncio
async def test_render_orchestration_maps_archive_validation_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        archive_client=_ArchiveClientValidationFailure(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_validation_failed"
    assert failed.failure_message == "Archive metadata was invalid."
    assert failed.retry_eligible is False


@pytest.mark.asyncio
async def test_render_orchestration_maps_archive_storage_failure_as_retryable(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        archive_client=_ArchiveClientStorageFailure(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_storage_failed"
    assert failed.retry_eligible is True


@pytest.mark.asyncio
async def test_render_orchestration_maps_archive_service_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        archive_client=_ArchiveClientExecutionFailure(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_execution_failed"
    assert failed.retry_eligible is False
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
        "archiving",
        "failed",
    ]


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
    assert payload["report_data"]["review_period_label"] == "YTD"
    assert payload["report_data"]["performance_periods"] == []
    assert payload["report_data"]["top_holdings"] == []
    assert payload["report_data"]["review_observations"] == [
        "Portfolio review was rendered from the governed lotus-report snapshot."
    ]


def test_build_render_package_emits_richer_report_contract(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-rich",
    )

    payload = _build_render_package(
        job=job,
        render_job_id="rdr-rich",
        snapshot={
            "portfolioName": "PB SG Global Balanced",
            "reviewPeriod": {"label": "YTD"},
            "readiness": {"status": "ready"},
            "reportingCurrency": "USD",
            "clientProfile": {
                "identity": {
                    "client_name": "Alex Tan",
                    "advisor_id": "RM_SG_001",
                    "booking_center_code": "Singapore",
                },
                "mandate_profile": {"risk_exposure": "balanced"},
            },
            "overview": {"currency": "USD", "total_market_value": "1000.50"},
            "allocation": {
                "byAssetClass": [
                    {
                        "group": "Equity",
                        "weight": 60.0,
                        "market_value": 600000.0,
                        "position_count": 3,
                    },
                    {
                        "group": "Fixed Income",
                        "weight": 35.0,
                        "market_value": 350000.0,
                        "position_count": 2,
                    },
                ],
                "byCurrency": [
                    {
                        "group": "USD",
                        "weight": 95.0,
                        "market_value": 950000.0,
                        "position_count": 5,
                    },
                    {
                        "group": "SGD",
                        "weight": 5.0,
                        "market_value": 50000.0,
                        "position_count": 1,
                    },
                ],
                "byRegion": [
                    {
                        "group": "North America",
                        "weight": 62.0,
                        "market_value": 620000.0,
                        "position_count": 4,
                    },
                    {
                        "group": "Asia",
                        "weight": 18.0,
                        "market_value": 180000.0,
                        "position_count": 1,
                    },
                ],
                "bySector": [
                    {
                        "group": "Technology",
                        "weight": 30.0,
                        "market_value": 300000.0,
                        "position_count": 2,
                    },
                    {
                        "group": "Healthcare",
                        "weight": 15.0,
                        "market_value": 150000.0,
                        "position_count": 1,
                    },
                ],
                "byCountry": [
                    {
                        "group": "United States",
                        "weight": 55.0,
                        "market_value": 550000.0,
                        "position_count": 3,
                    }
                ],
                "byProductType": [
                    {
                        "group": "Fund",
                        "weight": 95.0,
                        "market_value": 950000.0,
                        "position_count": 5,
                    }
                ],
                "byRating": [
                    {
                        "group": "A",
                        "weight": 40.0,
                        "market_value": 400000.0,
                        "position_count": 2,
                    }
                ],
            },
            "performance": {
                "summary": {
                    "YTD": {
                        "net_cumulative_return": 4.1,
                        "benchmark_cumulative_return": 3.4,
                        "benchmark_relative_return": 0.7,
                    }
                }
            },
            "riskAnalytics": {
                "summary": {
                    "YTD": {
                        "volatility": 12.0,
                        "beta": 0.82,
                        "tracking_error": 4.0,
                        "information_ratio": 0.72,
                        "value_at_risk": -2.0,
                    }
                }
            },
            "holdings": {
                "holdingsByAssetClass": {
                    "Equity": [
                        {
                            "security_id": "EQ-1",
                            "instrument_name": "Equity 1",
                            "isin": "US0000000001",
                            "quantity": 2200.0,
                            "position_date": "2026-04-22",
                            "product_type": "Fund",
                            "sector": "Technology",
                            "country_of_risk": "United States",
                            "rating": "A",
                            "liquidity_tier": "High",
                            "held_since_date": "2024-01-15",
                            "market_price": 102.35,
                            "cost_basis_reporting_currency": 500000.0,
                            "weight": 60.0,
                            "market_value_reporting_currency": 600000.0,
                            "unrealized_pnl_reporting_currency": 100000.0,
                            "unrealized_pnl_pct": 20.0,
                            "ytd_contribution_pct": 3.5,
                            "ytd_average_weight_pct": 55.0,
                            "ytd_total_return_pct": 8.4,
                            "currency": "USD",
                        }
                    ]
                }
            },
            "transactions": {
                "transactionsByCategory": {
                    "Trading": [
                        {
                            "transaction_id": "TXN-1",
                            "transaction_date": "2026-04-18",
                            "transaction_type": "SELL",
                            "instrument_id": "INST-1",
                            "security_id": "EQ-1",
                            "transaction_category": "Trading",
                            "display_label": "Sell",
                            "cash_leg": False,
                            "asset_class": "Equity",
                            "amount_reporting_currency": 25000.0,
                            "gross_transaction_amount_reporting_currency": 25000.0,
                            "realized_pnl_reporting_currency": 1250.0,
                            "realized_pnl_local": 1250.0,
                            "net_interest_amount_reporting_currency": 0.0,
                            "withholding_tax_amount_reporting_currency": 0.0,
                            "income_or_tax_reporting_currency": 0.0,
                        }
                    ]
                },
                "transactionCount": 1,
            },
            "reviewObservations": [
                {"summary": "Risk posture remained within the balanced mandate range."}
            ],
            "evidence": {
                "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
                "trust_metadata": {
                    "completeness_status": "complete",
                    "data_quality_status": "quality_passed",
                },
            },
            "keyFigures": {
                "client_profile": {
                    "objective": (
                        "Long-term real wealth growth with controlled income and liquidity."
                    )
                },
                "portfolio_value": {
                    "invested_market_value_reporting_currency": 998800.0,
                    "cash_balance_reporting_currency": 50000.0,
                    "cash_weight_pct": 5.0,
                },
                "allocation": {
                    "name": "Equity",
                    "weight_pct": 60.0,
                    "market_value_reporting_currency": 600000.0,
                    "position_count": 3,
                },
                "performance": {
                    "benchmark_comparison_status": "available",
                    "largest_positive_contributor": {
                        "security_name": "Equity 1",
                        "total_contribution_pct": 3.5,
                    },
                },
                "risk": {
                    "ytd_volatility_pct": 12.0,
                    "ytd_beta": 0.82,
                    "ytd_tracking_error_pct": 4.0,
                    "ytd_information_ratio": 0.72,
                },
                "holdings": {"position_count": 12},
            },
        },
    )

    report_data = payload["report_data"]
    assert report_data["portfolio_name"] == "PB SG Global Balanced"
    assert report_data["summary_paragraph"] == (
        "Risk posture remained within the balanced mandate range."
    )
    assert report_data["mandate"] == {
        "objective": "Long-term real wealth growth with controlled income and liquidity.",
        "risk_exposure": "balanced",
        "booking_center_code": "Singapore",
        "advisor_id": "RM_SG_001",
    }
    assert report_data["portfolio_metrics"] == {
        "invested_value": "998800.00",
        "cash_balance": "50000.00",
        "cash_weight_pct": "5.00%",
    }
    assert report_data["allocation_summary"] == {
        "largest_asset_class_name": "Equity",
        "largest_asset_class_weight_pct": "60.00%",
        "largest_asset_class_market_value": "600000.00",
        "largest_asset_class_position_count": 3,
    }
    assert report_data["allocation_breakdowns"] == {
        "by_asset_class": [
            {
                "name": "Equity",
                "weight_pct": "60.00%",
                "market_value": "600000.00",
                "position_count": 3,
            },
            {
                "name": "Fixed Income",
                "weight_pct": "35.00%",
                "market_value": "350000.00",
                "position_count": 2,
            },
        ],
        "by_currency": [
            {
                "name": "USD",
                "weight_pct": "95.00%",
                "market_value": "950000.00",
                "position_count": 5,
            },
            {
                "name": "SGD",
                "weight_pct": "5.00%",
                "market_value": "50000.00",
                "position_count": 1,
            },
        ],
        "by_region": [
            {
                "name": "North America",
                "weight_pct": "62.00%",
                "market_value": "620000.00",
                "position_count": 4,
            },
            {
                "name": "Asia",
                "weight_pct": "18.00%",
                "market_value": "180000.00",
                "position_count": 1,
            },
        ],
        "by_sector": [
            {
                "name": "Technology",
                "weight_pct": "30.00%",
                "market_value": "300000.00",
                "position_count": 2,
            },
            {
                "name": "Healthcare",
                "weight_pct": "15.00%",
                "market_value": "150000.00",
                "position_count": 1,
            },
        ],
        "by_country": [
            {
                "name": "United States",
                "weight_pct": "55.00%",
                "market_value": "550000.00",
                "position_count": 3,
            }
        ],
        "by_product_type": [
            {
                "name": "Fund",
                "weight_pct": "95.00%",
                "market_value": "950000.00",
                "position_count": 5,
            }
        ],
        "by_rating": [
            {
                "name": "A",
                "weight_pct": "40.00%",
                "market_value": "400000.00",
                "position_count": 2,
            }
        ],
    }
    assert report_data["performance_periods"] == [
        {
            "period": "YTD",
            "net_return_pct": "4.10%",
            "benchmark_return_pct": "3.40%",
            "relative_return_pct": "0.70%",
        }
    ]
    assert report_data["transaction_period_label"] == "From 01.01.2026 to 22.04.2026"
    assert report_data["risk_summary"] == {
        "volatility_pct": "12.00%",
        "beta": "0.82",
        "tracking_error_pct": "4.00%",
        "information_ratio": "0.72",
        "value_at_risk_pct": "-2.00%",
    }
    assert report_data["top_holdings"] == [
        {
            "asset_class": "Equity",
            "security_name": "Equity 1",
            "weight_pct": "60.00%",
            "quantity": "2200.00",
            "currency": "USD",
            "security_id": "EQ-1",
            "instrument_name": "Equity 1",
            "isin": "US0000000001",
            "position_date": "2026-04-22",
            "product_type": "Fund",
            "sector": "Technology",
            "country_of_risk": "United States",
            "rating": "A",
            "liquidity_tier": "High",
            "held_since_date": "2024-01-15",
            "market_price": "102.35",
            "cost_basis_reporting_currency": "500000.00",
            "cost_basis_local": "Not available",
            "market_value": "600000.00",
            "market_value_local": "Not available",
            "unrealized_pnl": "100000.00",
            "unrealized_pnl_local": "Not available",
            "unrealized_pnl_pct": "20.00%",
            "ytd_contribution_pct": "3.50%",
            "ytd_average_weight_pct": "55.00%",
            "ytd_total_return_pct": "8.40%",
        }
    ]
    assert report_data["positions"] == report_data["top_holdings"]
    assert report_data["transactions"] == [
        {
            "category": "Trading",
            "asset_class": "Equity",
            "transaction_category": "Trading",
            "display_label": "Sell",
            "cash_leg": "No",
            "transaction_id": "TXN-1",
            "trade_date": "2026-04-18",
            "transaction_type": "SELL",
            "instrument_id": "INST-1",
            "security_id": "EQ-1",
            "amount": "25000.00",
            "gross_amount_reporting_currency": "25000.00",
            "realized_pnl_reporting_currency": "1250.00",
            "realized_pnl_local": "1250.00",
            "net_interest_amount_reporting_currency": "0.00",
            "withholding_tax_amount_reporting_currency": "0.00",
            "income_or_tax_reporting_currency": "0.00",
        }
    ]
    assert report_data["governance_summary"] == {
        "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
        "completeness_status": "complete",
        "data_quality_status": "quality_passed",
        "readiness_status": "ready",
    }


def test_build_render_package_emits_outcome_review_contract(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_outcome_review_report_job(
        request=OutcomeReviewReportJobRequest.model_validate(
            {
                "outcome_report_input": {
                    "outcome_review_id": "dor_001",
                    "outcome_review_content_hash": "sha256:outcome-review",
                    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                    "proof_pack_id": "dpp_001",
                    "rebalance_run_id": "run_001",
                    "wave_id": "wave_001",
                    "review_window": {"start_date": "2026-04-22", "end_date": "2026-04-23"},
                    "report_title": "Post-Trade Outcome Review - PB_SG_GLOBAL_BAL_001",
                    "state": "READY",
                    "overall_outcome": "Execution outcome aligned with pre-trade proof.",
                    "dimensions": [
                        {
                            "dimension": "PERFORMANCE",
                            "state": "READY",
                            "reason_code": "performance_realized",
                            "expected": "4.10",
                            "realized": "4.22",
                            "variance": "0.12",
                            "explanation": "Realized performance exceeded expected performance.",
                        }
                    ],
                    "source_lineage": [{"source_system": "lotus-manage"}],
                    "source_hashes": {"realized": "sha256:realized"},
                    "section_hashes": {"proof_pack": "sha256:proof-pack"},
                    "content_hash": "sha256:report-input",
                    "redaction_policy": "NO_RAW_PAYLOADS",
                },
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-outcome-render",
    )

    payload = _build_render_package(
        job=job,
        snapshot=job.options["outcome_report_input"],
        render_job_id="rdr-outcome",
    )

    assert payload["report_type"] == "outcome_review"
    assert payload["report_data_contract_version"] == "dpm_outcome_report_input.v1"
    assert payload["template_id"] == "outcome-review"
    assert payload["report_data"]["outcome_review_id"] == "dor_001"
    assert payload["report_data"]["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
    assert payload["report_data"]["dimensions"][0]["variance"] == "0.12"
    assert payload["lineage_refs"] == [job.job_id, "dor_001", "sha256:report-input"]


def test_build_render_package_emits_proof_pack_contract(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_proof_pack_report_job(
        request=ProofPackReportJobRequest.model_validate(
            {
                "proof_pack_report_input": _proof_pack_report_input(),
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-proof-pack-render",
    )

    payload = _build_render_package(
        job=job,
        snapshot=job.options["proof_pack_report_input"],
        render_job_id="rdr-proof-pack",
    )

    assert payload["report_type"] == "proof_pack"
    assert payload["report_data_contract_version"] == "dpm_proof_pack_report_input.v1"
    assert payload["template_id"] == "proof-pack"
    assert payload["disclosure_refs"] == ["proof-pack.standard-disclosures.v1"]
    assert payload["report_data"]["proof_pack_id"] == "dpp_001"
    assert payload["report_data"]["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
    assert payload["report_data"]["proof_pack_content_hash"] == "sha256:proof-pack"
    assert payload["report_data"]["sections"][0]["title"] == "Mandate context"
    assert payload["lineage_refs"] == [job.job_id, "dpp_001", "sha256:report-input"]


def test_build_render_package_applies_proof_pack_fallbacks(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_proof_pack_report_job(
        request=ProofPackReportJobRequest.model_validate(
            {
                "proof_pack_report_input": {
                    "proof_pack_id": "dpp_minimal",
                    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                    "as_of_date": "2026-05-03",
                    "sections": [
                        {
                            "title": " ",
                            "reason_codes": [" ", "ready"],
                        },
                        "ignored",
                    ],
                    "source_hashes": "ignored",
                },
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-proof-pack-render-fallbacks",
    )

    payload = _build_render_package(
        job=job,
        snapshot=job.options["proof_pack_report_input"],
        render_job_id="rdr-proof-pack-fallbacks",
    )

    report_data = payload["report_data"]
    assert report_data["title"] == "Pre-Trade Proof Pack - PB_SG_GLOBAL_BAL_001"
    assert report_data["proof_pack_content_hash"] == "not_available"
    assert report_data["source_hashes"] == {}
    assert report_data["sections"] == [
        {
            "section_id": "not_available",
            "section_type": "not_available",
            "state": "not_available",
            "title": "Not available",
            "summary": "No section summary supplied.",
            "reason_codes": ["ready"],
            "content_hash": "not_available",
        }
    ]
    assert payload["lineage_refs"] == [job.job_id, "dpp_minimal", "not_available"]


def test_build_render_package_applies_outcome_review_fallbacks(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_outcome_review_report_job(
        request=OutcomeReviewReportJobRequest.model_validate(
            {
                "outcome_report_input": {
                    "outcome_review_id": "dor_minimal",
                    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                    "review_window": {"end_date": "2026-04-23"},
                    "dimensions": [
                        {
                            "dimension": " ",
                            "expected": "",
                        },
                        "ignored",
                    ],
                    "source_lineage": ["ignored"],
                    "source_hashes": "ignored",
                    "section_hashes": "ignored",
                },
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-outcome-render-fallbacks",
    )

    payload = _build_render_package(
        job=job,
        snapshot=job.options["outcome_report_input"],
        render_job_id="rdr-outcome-fallbacks",
    )

    report_data = payload["report_data"]
    assert report_data["title"] == "Post-Trade Outcome Review - PB_SG_GLOBAL_BAL_001"
    assert report_data["overall_outcome"] == "Outcome summary was not supplied."
    assert report_data["dimensions"] == [
        {
            "dimension": "not_available",
            "state": "not_available",
            "reason_code": "not_available",
            "expected": "not_available",
            "realized": "not_available",
            "variance": "not_available",
            "explanation": "No explanation supplied.",
        }
    ]
    assert report_data["source_services"] == ["lotus-manage"]
    assert report_data["source_hashes"] == {}
    assert report_data["section_hashes"] == {}
    assert report_data["content_hash"] == "not_available"
    assert payload["lineage_refs"] == [job.job_id, "dor_minimal", "not_available"]


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


def test_render_package_helpers_ignore_malformed_collection_rows(monkeypatch):
    assert (
        _performance_history(
            {"performance": {"monthly_history": "not-a-list"}}, "monthly_history", limit=3
        )
        == []
    )
    assert _performance_history(
        {"performance": {"monthly_history": [{"period": "2026-04"}, "bad-row"]}},
        "monthly_history",
        limit=3,
    ) == [
        {
            "period": "2026-04",
            "period_start": "Not available",
            "period_end": "Not available",
            "final_value": "Not available",
            "inflows": "Not available",
            "outflows": "Not available",
            "performance_value": "Not available",
            "cumulative_performance_value": "Not available",
            "twr_pct": "Not available",
            "cumulative_twr_pct": "Not available",
        }
    ]
    assert _transactions({"transactions": {"transactionsByCategory": {}}}) == []
    assert (
        _transactions(
            {
                "transactions": {
                    "transactionsByAssetClass": {
                        "Equity": [
                            "bad-row",
                            {
                                "transaction_id": "TXN-1",
                                "transaction_date": "2026-04-22",
                            },
                        ],
                        "Cash": "bad-group",
                    }
                }
            }
        )[0]["category"]
        == "Equity"
    )
    assert _allocation_bucket_rows("bad-buckets") == []
    assert _allocation_bucket_rows(
        [
            "bad-row",
            {"group": "Cash", "weight": "5", "market_value": "50", "position_count": "1"},
            {"group": "Equity", "weight": "95", "market_value": "950", "position_count": 5},
        ]
    ) == [
        {
            "name": "Equity",
            "weight_pct": "95.00%",
            "market_value": "950.00",
            "position_count": 5,
        },
        {
            "name": "Cash",
            "weight_pct": "5.00%",
            "market_value": "50.00",
            "position_count": 1,
        },
    ]
    assert _date_text("2026-04-22") == "2026-04-22"
    with pytest.raises(ValueError, match="date value is required"):
        _date_text(None)
    assert _archive_failure_posture(409, {}) == ("archive_conflict", False)
    assert _archive_failure_message({"detail": {}}) == "lotus-archive handoff failed."

    class _SentinelClient:
        pass

    sentinel_client = _SentinelClient()
    sentinel_archive_client = object()
    sentinel_store = object()
    sentinel_ledger = object()

    monkeypatch.setattr(render_service, "RenderClient", lambda **kwargs: sentinel_client)
    monkeypatch.setattr(
        render_service,
        "ArchiveClient",
        lambda **kwargs: sentinel_archive_client,
    )
    monkeypatch.setattr(render_service, "get_report_input_snapshot_store", lambda: sentinel_store)
    monkeypatch.setattr(render_service, "get_report_job_ledger", lambda: sentinel_ledger)
    render_service.get_portfolio_review_render_orchestration_service.cache_clear()
    try:
        service = render_service.get_portfolio_review_render_orchestration_service()
    finally:
        render_service.get_portfolio_review_render_orchestration_service.cache_clear()

    assert service._render_client is sentinel_client
    assert service._archive_client is sentinel_archive_client
    assert service._snapshot_store is sentinel_store
    assert service._job_ledger is sentinel_ledger
