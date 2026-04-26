from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher
from app.report_batch_orchestrator.execution import ReportBatchExecutionService
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    BatchDispatchPolicy,
    PortfolioBatchCandidate,
)
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger
from app.reporting_jobs.models import ReportCallerContext, ReportJobLedgerRecord
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_render.service import PortfolioReviewRenderOrchestrationService


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for batch execution proof")
    return database_url


def _caller(suffix: str) -> ReportCallerContext:
    return ReportCallerContext.model_validate(
        {
            "triggered_by": "advisor-123",
            "caller_application": "lotus-report-batch",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "booking_center_code": "SG",
            "role": "advisor",
            "correlation_id": f"corr-batch-execution-{suffix}",
            "trace_id": f"trace-batch-execution-{suffix}",
        }
    )


def _batch_request(suffix: str) -> BatchCreateRequest:
    portfolio_id = f"PB_SG_GLOBAL_BAL_001_{suffix}"
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=[portfolio_id],
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id=portfolio_id,
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
                selected=True,
            )
        ],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


class _CaptureService:
    def __init__(
        self,
        *,
        ledger: PostgresReportJobLedger,
        store: PostgresReportInputSnapshotStore,
    ) -> None:
        self._ledger = ledger
        self._store = store

    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
        ready = self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        self._store.create_snapshot(
            ReportInputSnapshotCreateRequest(
                report_job_id=job.job_id,
                report_type=job.report_type,
                report_data_contract_version="portfolio_review.v1",
                portfolio_scope=job.portfolio_scope,
                as_of_date=job.as_of_date,
                snapshot_payload={
                    "readiness": {"status": "ready"},
                    "reportingCurrency": "USD",
                    "reviewPeriod": {"label": "Year to date"},
                    "clientProfile": {
                        "identity": {
                            "client_name": "Alex Tan",
                            "advisor_id": "RM_SG_001",
                            "booking_center_code": "Singapore",
                        },
                        "mandate_profile": {"risk_exposure": "balanced"},
                    },
                    "overview": {"total_market_value": 1_523_456.78, "currency": "USD"},
                    "performance": {
                        "summary": {"YTD": {"net_cumulative_return": 4.1}},
                        "monthly_history": [],
                    },
                    "holdings": {"holdingsByAssetClass": {}},
                    "transactions": {"transactionsByCategory": {}, "transactionCount": 0},
                    "reviewObservations": [
                        {"summary": "Portfolio review data is ready for client reporting."}
                    ],
                    "evidence": {
                        "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
                        "trust_metadata": {
                            "completeness_status": "complete",
                            "data_quality_status": "quality_passed",
                        },
                    },
                    "keyFigures": {
                        "client_profile": {"objective": "Balanced long-term wealth growth."},
                        "portfolio_value": {
                            "invested_market_value_reporting_currency": 1_500_000.0,
                            "cash_balance_reporting_currency": 23_456.78,
                            "cash_weight_pct": 1.54,
                        },
                        "allocation": {
                            "name": "Equity",
                            "weight_pct": 60.0,
                            "market_value_reporting_currency": 900_000.0,
                            "position_count": 8,
                        },
                        "holdings": {"position_count": 8},
                    },
                },
                snapshot_storage_ref=None,
                supportability_status="complete",
                completeness_status="complete",
                lineage_summary={
                    "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
                    "call_count": 3,
                },
                captured_at=datetime.now(UTC),
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        )
        return ready


class _RenderClientSuccess:
    async def submit_render_package(self, payload, correlation_id=None):
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


class _ArchiveClientSuccess:
    def __init__(self):
        self.payloads: list[dict] = []

    async def archive_document(self, payload, **kwargs):
        self.payloads.append(payload)
        return 201, {"document_id": "doc_batch_execution_archived"}


@pytest.mark.asyncio
async def test_postgres_batch_item_execution_archives_pdf_report_and_reconciles_batch() -> None:
    database_url = _database_url()
    suffix = uuid4().hex
    batch_ledger = PostgresReportBatchLedger(database_url)
    report_job_ledger = PostgresReportJobLedger(database_url)
    snapshot_store = PostgresReportInputSnapshotStore(database_url)
    archive_client = _ArchiveClientSuccess()
    caller = _caller(suffix)
    batch = batch_ledger.create_batch(
        request=_batch_request(suffix),
        caller_context=caller,
        idempotency_key=f"batch-pg-execution-{suffix}",
    )
    dispatched = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_batches=1000, max_active_items=1000),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=caller,
        worker_id=f"pg-worker-execution-{suffix}",
    )
    waiting_item = batch_ledger.get_batch(batch.batch_id).items[0]
    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        capture_service=_CaptureService(ledger=report_job_ledger, store=snapshot_store),
        render_service=PortfolioReviewRenderOrchestrationService(
            render_client=_RenderClientSuccess(),
            archive_client=archive_client,
            snapshot_store=snapshot_store,
            job_ledger=report_job_ledger,
        ),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=waiting_item.batch_item_id,
    )

    refreshed_batch = batch_ledger.get_batch(batch.batch_id)
    refreshed_job = report_job_ledger.get_job(waiting_item.report_job_id or "")
    snapshot = snapshot_store.get_snapshot_by_job(refreshed_job.job_id)
    assert dispatched.dispatched_count == 1
    assert result.item_status == "succeeded"
    assert result.report_job_status == "archived"
    assert refreshed_batch.status == "completed"
    assert refreshed_batch.items[0].status == "succeeded"
    assert refreshed_job.status == "archived"
    assert refreshed_job.archive_document_id == "doc_batch_execution_archived"
    assert snapshot.report_job_id == refreshed_job.job_id
    assert archive_client.payloads
    assert archive_client.payloads[0]["metadata"]["snapshot_id"] == snapshot.snapshot_id
    assert archive_client.payloads[0]["metadata"]["report_job_id"] == refreshed_job.job_id
