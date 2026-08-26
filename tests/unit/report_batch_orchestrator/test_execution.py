from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher
from app.report_batch_orchestrator.execution import ReportBatchExecutionService
from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    BatchDispatchPolicy,
    PortfolioBatchCandidate,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.reporting_jobs.ledger import ReportJobLedger, ReportJobNotFoundError
from app.reporting_jobs.models import ReportCallerContext, ReportJobLedgerRecord
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.service import PortfolioReviewRenderOrchestrationService


def _caller() -> ReportCallerContext:
    suffix = uuid4().hex
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


def _batch_request(*, requested_output_formats: list[str] | None = None) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id="PB_SG_GLOBAL_BAL_001",
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
                selected=True,
            )
        ],
        as_of_date="2026-04-22",
        requested_output_formats=requested_output_formats or ["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


def _dispatched_batch(tmp_path, *, requested_output_formats: list[str] | None = None):
    batch_ledger = ReportBatchLedger(tmp_path / f"batch-{uuid4().hex}.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / f"jobs-{uuid4().hex}.sqlite3")
    caller = _caller()
    batch = batch_ledger.create_batch(
        request=_batch_request(requested_output_formats=requested_output_formats),
        caller_context=caller,
        idempotency_key=f"batch-execution-{uuid4().hex}",
    )
    dispatch = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=5),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=caller,
        worker_id="worker-execution",
    )
    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert dispatch.dispatched_count == 1
    return batch_ledger, report_job_ledger, refreshed, refreshed.items[0]


class _CaptureService:
    def __init__(self, *, ledger: ReportJobLedger, store: ReportInputSnapshotStore):
        self._ledger = ledger
        self._store = store
        self.calls = 0

    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
        self.calls += 1
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
                        "summary": {
                            "YTD": {
                                "net_cumulative_return": 4.1,
                                "benchmark_cumulative_return": 3.4,
                                "benchmark_relative_return": 0.7,
                            }
                        },
                        "monthly_history": [
                            {
                                "period": "2026-04",
                                "period_start": "2026-04-01",
                                "period_end": "2026-04-22",
                                "end_market_value": 1_523_456.78,
                                "inflows": 10_000.0,
                                "outflows": -2_000.0,
                                "performance_value": 12_000.0,
                                "cumulative_performance_value": 12_000.0,
                                "twr_pct": 0.8,
                                "cumulative_twr_pct": 4.1,
                            }
                        ],
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
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        assert payload["report_job_id"].startswith("rjob_")
        assert payload["snapshot_id"].startswith("snapshot-for-rjob_")
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
            "artifact_base64": "JVBERi0xLjQKJQ==",
        }


class _RenderClientValidationFailure:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 422, {
            "detail": {
                "code": "render_package_invalid",
                "message": "Template payload was invalid.",
            }
        }


class _ArchiveClientSuccess:
    def __init__(self):
        self.payloads: list[dict] = []

    async def archive_document(self, payload, **kwargs):
        self.payloads.append(payload)
        assert kwargs["tenant_id"] == "tenant-sg"
        assert kwargs["region"] == "APAC"
        assert payload["metadata"]["report_job_id"].startswith("rjob_")
        assert payload["metadata"]["snapshot_id"].startswith("rsnap_")
        assert payload["metadata"]["render_job_id"].startswith("rdr_")
        assert payload["metadata"]["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
        assert "retention_policy_id" in payload["metadata"]
        assert "retention_start_date" in payload["metadata"]
        return 201, {"document_id": "doc_archived"}


class _ArchiveClientStorageFailure:
    async def archive_document(self, payload, **kwargs):
        return 503, {"detail": "archive storage unavailable"}


class _FailingRenderService:
    async def render_for_job(self, job):
        raise RuntimeError("render service unavailable")


class _UnusedCaptureService:
    async def capture_for_job(self, job):
        raise AssertionError("capture should not run")


class _StaticReportJobLedger:
    def __init__(self, job: ReportJobLedgerRecord):
        self._job = job

    def get_job(self, job_id: str) -> ReportJobLedgerRecord:
        assert job_id == self._job.job_id
        return self._job


class _UnusedReportJobLedger:
    def get_job(self, job_id: str) -> ReportJobLedgerRecord:
        raise AssertionError("report job should not be loaded")


class _StaticBatchLedger:
    def __init__(self, batch: ReportBatchRecord):
        self._batch = batch

    def get_batch(self, batch_id: str) -> ReportBatchRecord:
        assert batch_id == self._batch.batch_id
        return self._batch

    def mark_item_succeeded(self, **kwargs):
        raise AssertionError("static tests do not mark success")

    def mark_item_failed(
        self,
        *,
        batch_item_id: str,
        error_category: str,
        error_summary: str,
        retryable: bool,
        retry_policy=None,
    ):
        item = next(item for item in self._batch.items if item.batch_item_id == batch_item_id)
        failed_item = item.model_copy(
            update={
                "status": "failed_retryable" if retryable else "failed_terminal",
                "last_error_category": error_category,
                "last_error_summary": error_summary,
                "retry_eligible": retryable,
            }
        )
        return failed_item


def _execution_service(
    *,
    batch_ledger,
    report_job_ledger,
    store,
    render_client,
    archive_client,
):
    return ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        capture_service=_CaptureService(ledger=report_job_ledger, store=store),
        render_service=PortfolioReviewRenderOrchestrationService(
            render_client=render_client,
            archive_client=archive_client,
            snapshot_store=store,
            job_ledger=report_job_ledger,
        ),
    )


@pytest.mark.asyncio
async def test_batch_item_execution_captures_renders_archives_and_marks_item_succeeded(
    tmp_path,
) -> None:
    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    archive_client = _ArchiveClientSuccess()
    service = _execution_service(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        store=store,
        render_client=_RenderClientSuccess(),
        archive_client=archive_client,
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    job = report_job_ledger.get_job(item.report_job_id or "")
    snapshot = store.get_snapshot_by_job(job.job_id)
    assert result.item_status == "succeeded"
    assert result.report_job_status == "archived"
    assert refreshed.status == "completed"
    assert refreshed.items[0].status == "succeeded"
    assert refreshed.items[0].completed_at is not None
    assert job.status == "archived"
    assert job.archive_document_id == "doc_archived"
    assert snapshot.snapshot_payload["evidence"]["source_services"] == [
        "lotus-core",
        "lotus-performance",
        "lotus-risk",
    ]
    assert archive_client.payloads[0]["metadata"]["snapshot_id"] == snapshot.snapshot_id


@pytest.mark.asyncio
async def test_batch_item_execution_propagates_render_failure_to_item(tmp_path) -> None:
    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    service = _execution_service(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        store=store,
        render_client=_RenderClientValidationFailure(),
        archive_client=_ArchiveClientSuccess(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    job = report_job_ledger.get_job(item.report_job_id or "")
    assert result.item_status == "failed_terminal"
    assert result.failure_category == "render_validation_failed"
    assert result.retry_eligible is False
    assert refreshed.status == "completed_with_failures"
    assert refreshed.items[0].last_error_category == "render_validation_failed"
    assert job.status == "failed"
    assert job.failure_category == "render_validation_failed"


@pytest.mark.asyncio
async def test_batch_item_execution_propagates_archive_failure_to_item(tmp_path) -> None:
    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    service = _execution_service(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        store=store,
        render_client=_RenderClientSuccess(),
        archive_client=_ArchiveClientStorageFailure(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    job = report_job_ledger.get_job(item.report_job_id or "")
    assert result.item_status == "failed_retryable"
    assert result.failure_category == "archive_storage_failed"
    assert result.retry_eligible is True
    assert refreshed.status == "failed"
    assert refreshed.items[0].last_error_category == "archive_storage_failed"
    assert refreshed.items[0].retry_eligible is True
    assert job.status == "failed"
    assert job.failure_category == "archive_storage_failed"


@pytest.mark.asyncio
async def test_batch_item_execution_marks_non_pdf_data_ready_job_succeeded(tmp_path) -> None:
    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(
        tmp_path,
        requested_output_formats=["json"],
    )
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    capture_service = _CaptureService(ledger=report_job_ledger, store=store)

    class _UnusedRenderService:
        async def render_for_job(self, job):
            raise AssertionError("json-only report jobs should not render")

    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        capture_service=capture_service,
        render_service=_UnusedRenderService(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert result.item_status == "succeeded"
    assert result.report_job_status == "data_ready"
    assert capture_service.calls == 1
    assert refreshed.status == "completed"
    assert refreshed.items[0].status == "succeeded"


@pytest.mark.asyncio
async def test_batch_item_execution_marks_unexpected_execution_error_retryable(
    tmp_path,
) -> None:
    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        capture_service=_CaptureService(ledger=report_job_ledger, store=store),
        render_service=_FailingRenderService(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert result.item_status == "failed_retryable"
    assert result.report_job_status == "unknown"
    assert result.failure_category == "batch_execution_failed"
    assert result.retry_eligible is True
    assert refreshed.items[0].last_error_summary == "render service unavailable"


@pytest.mark.asyncio
async def test_batch_item_execution_returns_waiting_item_for_non_terminal_job(
    tmp_path,
) -> None:
    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    job = report_job_ledger.get_job(item.report_job_id or "").model_copy(
        update={"status": "queued", "current_step": "queued"}
    )
    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=_StaticReportJobLedger(job),
        capture_service=_CaptureService(
            ledger=report_job_ledger,
            store=ReportInputSnapshotStore(tmp_path / "lineage.sqlite3"),
        ),
        render_service=_FailingRenderService(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    assert result.item_status == "waiting_on_report_job"
    assert result.report_job_status == "queued"


@pytest.mark.asyncio
async def test_batch_item_execution_uses_safe_defaults_for_failed_job_without_details(
    tmp_path,
) -> None:
    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    job = report_job_ledger.get_job(item.report_job_id or "").model_copy(
        update={
            "status": "failed",
            "failure_category": None,
            "failure_message": None,
            "retry_eligible": False,
        }
    )
    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=_StaticReportJobLedger(job),
        capture_service=_CaptureService(
            ledger=report_job_ledger,
            store=ReportInputSnapshotStore(tmp_path / "lineage.sqlite3"),
        ),
        render_service=_FailingRenderService(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert result.item_status == "failed_terminal"
    assert result.failure_category == "report_job_failed"
    assert refreshed.items[0].last_error_summary == "Report job failed."


@pytest.mark.asyncio
async def test_batch_item_execution_rejects_invalid_batch_item_state(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = batch_ledger.create_batch(
        request=_batch_request(),
        caller_context=_caller(),
        idempotency_key="batch-invalid-state",
    )
    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=_UnusedReportJobLedger(),
        capture_service=_CaptureService(
            ledger=ReportJobLedger(tmp_path / "jobs-2.sqlite3"),
            store=ReportInputSnapshotStore(tmp_path / "lineage.sqlite3"),
        ),
        render_service=_FailingRenderService(),
    )

    with pytest.raises(ValueError, match="batch_item_not_waiting_on_report_job"):
        await service.execute_item(
            batch_id=batch.batch_id,
            batch_item_id=batch.items[0].batch_item_id,
        )
    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        await service.execute_item(batch_id=batch.batch_id, batch_item_id="missing")


@pytest.mark.asyncio
async def test_batch_item_execution_rejects_waiting_item_without_report_job(tmp_path) -> None:
    batch = ReportBatchRecord(
        batch_id="rbch_static",
        selector_mode="explicit_portfolio_list",
        tenant_id="tenant-sg",
        region="APAC",
        materialized_portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
        as_of_date=datetime(2026, 4, 22, tzinfo=UTC).date(),
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={},
        idempotency_key="static",
        request_hash="hash",
        status="running",
        item_count=1,
        created_at=datetime(2026, 4, 22, tzinfo=UTC),
        correlation_id="corr-static",
        trace_id="trace-static",
        items=[
            ReportBatchItemRecord(
                batch_item_id="rbit_static",
                batch_id="rbch_static",
                item_position=1,
                portfolio_id="PB_SG_GLOBAL_BAL_001",
                item_idempotency_key="static-item",
                status="waiting_on_report_job",
                source_system="lotus-core",
                source_object="PortfolioScope",
                created_at=datetime(2026, 4, 22, tzinfo=UTC),
            )
        ],
    )
    service = ReportBatchExecutionService(
        batch_ledger=_StaticBatchLedger(batch),
        report_job_ledger=_UnusedReportJobLedger(),
        capture_service=_UnusedCaptureService(),
        render_service=_FailingRenderService(),
    )

    with pytest.raises(ValueError, match="batch_item_report_job_missing"):
        await service.execute_item(batch_id=batch.batch_id, batch_item_id="rbit_static")


class _JobLedgerWithForeignTenant:
    """A report-job ledger whose linked job belongs to a different tenant than its batch.

    Reproduces the residual #170 cannot reach by admitting the batch record: a link created
    by a worker that was not yet tenant-scoped points at another tenant's report job. The
    mismatch is on the far side of the link, so no check on the batch can see it.
    """

    def __init__(self, *, inner: ReportJobLedger, foreign_tenant_id: str) -> None:
        self._inner = inner
        self._foreign_tenant_id = foreign_tenant_id
        self.executed_job_ids: list[str] = []

    def get_job(self, job_id: str) -> ReportJobLedgerRecord:
        self.executed_job_ids.append(job_id)
        job = self._inner.get_job(job_id)
        return job.model_copy(update={"tenant_id": self._foreign_tenant_id})


@pytest.mark.asyncio
async def test_batch_item_execution_quarantines_a_cross_tenant_report_job(tmp_path) -> None:
    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    foreign_ledger = _JobLedgerWithForeignTenant(
        inner=report_job_ledger,
        foreign_tenant_id="tenant-uk",
    )

    class _CaptureMustNotRun:
        async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("A cross-tenant report job must never be executed.")

    class _RenderMustNotRun:
        async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("A cross-tenant report job must never be rendered.")

    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=foreign_ledger,
        capture_service=_CaptureMustNotRun(),
        render_service=_RenderMustNotRun(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    assert result.failure_category == "batch_item_tenant_mismatch"
    assert result.report_job_status == "not_executed"
    assert result.item_status == "failed_terminal"
    assert result.retry_eligible is False


@pytest.mark.asyncio
async def test_quarantined_cross_tenant_item_is_never_retried(tmp_path) -> None:
    """A quarantine an operator has not resolved must not be resurrected by retry or scan."""

    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    foreign_ledger = _JobLedgerWithForeignTenant(
        inner=report_job_ledger,
        foreign_tenant_id="tenant-uk",
    )

    class _MustNotRun:
        async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("must not run")

        async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("must not run")

    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=foreign_ledger,
        capture_service=_MustNotRun(),
        render_service=_MustNotRun(),
    )
    await service.execute_item(batch_id=batch.batch_id, batch_item_id=item.batch_item_id)

    quarantined = batch_ledger.get_batch_item(batch.batch_id, item.batch_item_id)
    assert quarantined.status == "failed_terminal"
    assert quarantined.retry_eligible is False

    batch_ledger.retry_failed_items(batch_id=batch.batch_id)
    after_retry = batch_ledger.get_batch_item(batch.batch_id, item.batch_item_id)
    assert after_retry.status == "failed_terminal"

    runnable = batch_ledger.list_runnable_batch_ids(tenant_id=batch.tenant_id, limit=10)
    assert batch.batch_id not in runnable


@pytest.mark.asyncio
async def test_batch_item_execution_proceeds_when_the_linked_job_tenant_matches(
    tmp_path,
) -> None:
    """The comparison must be a no-op on the ordinary path."""

    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    archive_client = _ArchiveClientSuccess()
    service = _execution_service(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        store=store,
        render_client=_RenderClientSuccess(),
        archive_client=archive_client,
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    assert result.failure_category is None
    assert result.item_status == "succeeded"


class _JobLedgerWithMissingJob:
    """report_batch_item.report_job_id has no foreign key and jobs live in another ledger."""

    def __init__(self) -> None:
        self.lookups = 0

    def get_job(self, job_id: str):
        self.lookups += 1
        raise ReportJobNotFoundError("report_job_not_found")


class _JobLedgerWithTransientFault:
    """A connection or query fault, not an absent row."""

    def __init__(self) -> None:
        self.lookups = 0

    def get_job(self, job_id: str):
        self.lookups += 1
        raise RuntimeError("connection reset by peer")


@pytest.mark.asyncio
async def test_a_missing_linked_job_is_quarantined_not_raised(tmp_path) -> None:
    """An absent linked job must not escape execute_item and kill the worker pass."""

    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)

    class _MustNotRun:
        async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("must not run")

        async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("must not run")

    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=_JobLedgerWithMissingJob(),
        capture_service=_MustNotRun(),
        render_service=_MustNotRun(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    assert result.failure_category == "batch_item_report_job_missing"
    assert result.item_status == "failed_terminal"
    assert result.retry_eligible is False


@pytest.mark.asyncio
async def test_a_missing_linked_job_does_not_stall_the_rest_of_the_pass(tmp_path) -> None:
    """One broken link must not stop every other tenant's batches advancing.

    The lookup runs before the execution try-block, so an exception here would leave the
    item waiting and take down the pass on the same row every time.
    """

    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)

    class _MustNotRun:
        async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("must not run")

        async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("must not run")

    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=_JobLedgerWithMissingJob(),
        capture_service=_MustNotRun(),
        render_service=_MustNotRun(),
    )

    first = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    assert first.item_status == "failed_terminal"
    quarantined = batch_ledger.get_batch_item(batch.batch_id, item.batch_item_id)
    assert quarantined.status == "failed_terminal"
    assert quarantined.retry_eligible is False
    assert batch.batch_id not in batch_ledger.list_runnable_batch_ids(
        tenant_id=batch.tenant_id,
        limit=10,
    )


@pytest.mark.asyncio
async def test_a_transient_ledger_fault_is_not_mistaken_for_a_dangling_link(tmp_path) -> None:
    """Quarantine is permanent, so a brief outage must not be read as a data defect.

    Misclassifying a connection fault would terminally fail every waiting item whose
    batch-ledger write then succeeded — a worse outage than the one the lookup prevents.
    """

    batch_ledger, report_job_ledger, batch, item = _dispatched_batch(tmp_path)

    class _MustNotRun:
        async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("must not run")

        async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
            raise AssertionError("must not run")

    service = ReportBatchExecutionService(
        batch_ledger=batch_ledger,
        report_job_ledger=_JobLedgerWithTransientFault(),
        capture_service=_MustNotRun(),
        render_service=_MustNotRun(),
    )

    result = await service.execute_item(
        batch_id=batch.batch_id,
        batch_item_id=item.batch_item_id,
    )

    assert result.failure_category != "batch_item_report_job_missing"
    assert result.item_status == "failed_retryable"
    assert result.retry_eligible is True
    surviving = batch_ledger.get_batch_item(batch.batch_id, item.batch_item_id)
    assert surviving.status == "failed_retryable"
