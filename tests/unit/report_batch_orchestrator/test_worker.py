from __future__ import annotations

from uuid import uuid4

import pytest

from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher
from app.report_batch_orchestrator.execution import BatchItemExecutionResult
from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    BatchDispatchPolicy,
    BatchRuntimeLoad,
    PortfolioBatchCandidate,
)
from app.report_batch_orchestrator.worker import ReportBatchWorker
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import ReportCallerContext


def _caller() -> ReportCallerContext:
    suffix = uuid4().hex
    return ReportCallerContext.model_validate(
        {
            "triggered_by": "advisor-123",
            "caller_application": "lotus-report-batch-worker",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "booking_center_code": "SG",
            "role": "advisor",
            "correlation_id": f"corr-batch-worker-{suffix}",
            "trace_id": f"trace-batch-worker-{suffix}",
        }
    )


def _candidate(portfolio_id: str) -> PortfolioBatchCandidate:
    return PortfolioBatchCandidate(
        portfolio_id=portfolio_id,
        tenant_id="tenant-sg",
        region="APAC",
        active=True,
        selected=True,
    )


def _request(*portfolio_ids: str) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=list(portfolio_ids),
        source_candidates=[_candidate(portfolio_id) for portfolio_id in portfolio_ids],
        as_of_date="2026-04-22",
        requested_output_formats=["json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


class _SucceedingExecutionService:
    def __init__(self, *, batch_ledger: ReportBatchLedger) -> None:
        self._batch_ledger = batch_ledger
        self.executed_item_ids: list[str] = []

    async def execute_item(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
    ) -> BatchItemExecutionResult:
        batch = self._batch_ledger.get_batch(batch_id)
        item = next(item for item in batch.items if item.batch_item_id == batch_item_id)
        if item.report_job_id is None:
            raise AssertionError("worker should only execute dispatched items with report jobs")
        completed = self._batch_ledger.mark_item_succeeded(
            batch_item_id=batch_item_id,
            report_job_id=item.report_job_id,
        )
        self.executed_item_ids.append(batch_item_id)
        return BatchItemExecutionResult(
            batch_id=batch_id,
            batch_item_id=batch_item_id,
            report_job_id=item.report_job_id,
            item_status=completed.status,
            report_job_status="completed",
        )


def _worker(
    *,
    batch_ledger: ReportBatchLedger,
    report_job_ledger: ReportJobLedger,
    policy: BatchDispatchPolicy | None = None,
    execution_service: _SucceedingExecutionService | None = None,
) -> ReportBatchWorker:
    return ReportBatchWorker(
        batch_ledger=batch_ledger,
        dispatcher=ReportBatchDispatcher(
            batch_ledger=batch_ledger,
            report_job_ledger=report_job_ledger,
            policy=policy or BatchDispatchPolicy(max_active_items=5),
        ),
        execution_service=execution_service
        or _SucceedingExecutionService(batch_ledger=batch_ledger),
    )


@pytest.mark.asyncio
async def test_worker_run_dispatches_and_executes_runnable_batch(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002"),
        caller_context=_caller(),
        idempotency_key=f"worker-run-{uuid4().hex}",
    )
    execution_service = _SucceedingExecutionService(batch_ledger=batch_ledger)

    result = await _worker(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        execution_service=execution_service,
    ).run_once(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-unit-1",
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert result.batch_status_before == "materialized"
    assert result.batch_status_after == "completed"
    assert result.recovered_count == 0
    assert result.leased_count == 2
    assert result.dispatched_count == 2
    assert result.executed_count == 2
    assert result.back_pressure_reasons == []
    assert len(result.report_job_ids) == 2
    assert len(execution_service.executed_item_ids) == 2
    assert refreshed.status == "completed"
    assert {item.status for item in refreshed.items} == {"succeeded"}
    assert [
        report_job_ledger.get_job(job_id).portfolio_scope["portfolio_ids"][0]
        for job_id in result.report_job_ids
    ] == ["PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002"]


@pytest.mark.asyncio
async def test_worker_run_is_noop_for_paused_batch(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=_caller(),
        idempotency_key=f"worker-paused-{uuid4().hex}",
    )
    batch_ledger.pause_batch(batch_id=batch.batch_id)

    result = await _worker(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
    ).run_once(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-unit-1",
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert result.skipped_reason == "batch_not_runnable:paused"
    assert result.dispatched_count == 0
    assert result.executed_count == 0
    assert refreshed.status == "paused"
    assert {item.status for item in refreshed.items} == {"materialized"}


@pytest.mark.asyncio
async def test_worker_executes_existing_waiting_items_under_dispatch_back_pressure(
    tmp_path,
) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    caller = _caller()
    batch = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=caller,
        idempotency_key=f"worker-waiting-{uuid4().hex}",
    )
    ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=5),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=caller,
        worker_id="worker-dispatch",
    )

    result = await _worker(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=1),
    ).run_once(
        batch_id=batch.batch_id,
        caller_context=caller,
        worker_id="worker-unit-1",
        runtime_load=BatchRuntimeLoad(active_items=1),
    )

    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert result.back_pressure_reasons == ["max_active_items_reached"]
    assert result.dispatched_count == 0
    assert result.executed_count == 1
    assert refreshed.status == "completed"
    assert refreshed.items[0].status == "succeeded"


def _caller_for(tenant_id: str) -> ReportCallerContext:
    return _caller().model_copy(update={"tenant_id": tenant_id})


@pytest.mark.asyncio
async def test_worker_run_once_rejects_a_cross_tenant_caller(tmp_path) -> None:
    """run_once is reachable from an operator route, so it must admit before mutating."""

    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=_caller_for("tenant-sg"),
        idempotency_key=f"worker-cross-tenant-{uuid4().hex}",
    )
    worker = ReportBatchWorker(
        batch_ledger=batch_ledger,
        dispatcher=ReportBatchDispatcher(
            batch_ledger=batch_ledger,
            report_job_ledger=report_job_ledger,
            policy=BatchDispatchPolicy(max_active_items=5),
        ),
        execution_service=_SucceedingExecutionService(batch_ledger=batch_ledger),
    )

    with pytest.raises(ValueError) as excinfo:
        await worker.run_once(
            batch_id=batch.batch_id,
            caller_context=_caller_for("tenant-uk"),
            worker_id="worker-cross-tenant-1",
        )

    assert str(excinfo.value) == "report_batch_not_found"
    untouched = batch_ledger.get_batch(batch.batch_id)
    assert untouched.status == "materialized"
    assert [item.status for item in untouched.items] == ["materialized"]
    assert [item.report_job_id for item in untouched.items] == [None]
    assert [item.lease_token for item in untouched.items] == [None]


@pytest.mark.asyncio
async def test_worker_run_once_does_not_leak_batch_status_to_a_cross_tenant_caller(
    tmp_path,
) -> None:
    """Admission runs before the runnable-status check, so no status is returned."""

    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=_caller_for("tenant-sg"),
        idempotency_key=f"worker-status-leak-{uuid4().hex}",
    )
    batch_ledger.pause_batch(batch_id=batch.batch_id)
    worker = ReportBatchWorker(
        batch_ledger=batch_ledger,
        dispatcher=ReportBatchDispatcher(
            batch_ledger=batch_ledger,
            report_job_ledger=report_job_ledger,
            policy=BatchDispatchPolicy(max_active_items=5),
        ),
        execution_service=_SucceedingExecutionService(batch_ledger=batch_ledger),
    )

    with pytest.raises(ValueError) as excinfo:
        await worker.run_once(
            batch_id=batch.batch_id,
            caller_context=_caller_for("tenant-uk"),
            worker_id="worker-status-leak-1",
        )

    assert "paused" not in str(excinfo.value)
    assert str(excinfo.value) == "report_batch_not_found"
