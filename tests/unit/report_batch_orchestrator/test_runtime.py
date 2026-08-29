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
from app.report_batch_orchestrator.runtime import ReportBatchRuntime
from app.report_batch_orchestrator.worker import ReportBatchWorker
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import ReportCallerContext


def _caller() -> ReportCallerContext:
    suffix = uuid4().hex
    return ReportCallerContext.model_validate(
        {
            "triggered_by": "advisor-123",
            "caller_application": "lotus-report-batch-runtime",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "booking_center_code": "SG",
            "role": "advisor",
            "correlation_id": f"corr-batch-runtime-{suffix}",
            "trace_id": f"trace-batch-runtime-{suffix}",
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


def _request(portfolio_id: str) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=[portfolio_id],
        source_candidates=[_candidate(portfolio_id)],
        as_of_date="2026-04-22",
        requested_output_formats=["json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


class _SucceedingExecutionService:
    def __init__(self, *, batch_ledger: ReportBatchLedger) -> None:
        self._batch_ledger = batch_ledger

    async def execute_item(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
    ) -> BatchItemExecutionResult:
        batch = self._batch_ledger.get_batch(batch_id)
        item = next(item for item in batch.items if item.batch_item_id == batch_item_id)
        if item.report_job_id is None:
            raise AssertionError("runtime should execute only job-linked items")
        completed = self._batch_ledger.mark_item_succeeded(
            batch_item_id=batch_item_id,
            report_job_id=item.report_job_id,
        )
        return BatchItemExecutionResult(
            batch_id=batch_id,
            batch_item_id=batch_item_id,
            report_job_id=item.report_job_id,
            item_status=completed.status,
            report_job_status="completed",
        )


def _runtime(
    *,
    batch_ledger: ReportBatchLedger,
    report_job_ledger: ReportJobLedger,
    policy: BatchDispatchPolicy | None = None,
) -> ReportBatchRuntime:
    return ReportBatchRuntime(
        batch_ledger=batch_ledger,
        worker=ReportBatchWorker(
            batch_ledger=batch_ledger,
            dispatcher=ReportBatchDispatcher(
                batch_ledger=batch_ledger,
                report_job_ledger=report_job_ledger,
                policy=policy or BatchDispatchPolicy(max_active_items=5),
            ),
            execution_service=_SucceedingExecutionService(batch_ledger=batch_ledger),
        ),
    )


@pytest.mark.asyncio
async def test_runtime_pass_scans_and_runs_multiple_batches(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    caller = _caller()
    first = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=caller,
        idempotency_key=f"runtime-first-{uuid4().hex}",
    )
    second = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_002"),
        caller_context=caller,
        idempotency_key=f"runtime-second-{uuid4().hex}",
    )

    result = await _runtime(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
    ).run_pass(
        caller_context=caller,
        worker_id="runtime-unit-1",
        max_batches=5,
    )

    assert result.worker_id == "runtime-unit-1"
    assert result.scanned_batch_ids == [first.batch_id, second.batch_id]
    assert [batch_result.batch_id for batch_result in result.batch_results] == [
        first.batch_id,
        second.batch_id,
    ]
    assert result.dispatched_count == 2
    assert result.executed_count == 2
    assert result.back_pressure_stopped is False
    assert batch_ledger.get_batch(first.batch_id).status == "completed"
    assert batch_ledger.get_batch(second.batch_id).status == "completed"


@pytest.mark.asyncio
async def test_runtime_pass_stops_on_back_pressure_without_advancing_later_batches(
    tmp_path,
) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    caller = _caller()
    first = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=caller,
        idempotency_key=f"runtime-pressure-first-{uuid4().hex}",
    )
    second = batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_002"),
        caller_context=caller,
        idempotency_key=f"runtime-pressure-second-{uuid4().hex}",
    )

    result = await _runtime(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=1),
    ).run_pass(
        caller_context=caller,
        worker_id="runtime-unit-1",
        runtime_load=BatchRuntimeLoad(active_items=1),
    )

    assert result.scanned_batch_ids == [first.batch_id, second.batch_id]
    assert [batch_result.batch_id for batch_result in result.batch_results] == [first.batch_id]
    assert result.back_pressure_stopped is True
    assert result.back_pressure_reasons == ["max_active_items_reached"]
    assert result.dispatched_count == 0
    assert result.executed_count == 0
    assert batch_ledger.get_batch(first.batch_id).status == "materialized"
    assert batch_ledger.get_batch(second.batch_id).status == "materialized"


@pytest.mark.asyncio
async def test_runtime_pass_honors_zero_max_batches_without_scanning(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    caller = _caller()
    batch_ledger.create_batch(
        request=_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=caller,
        idempotency_key=f"runtime-zero-{uuid4().hex}",
    )

    result = await _runtime(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
    ).run_pass(
        caller_context=caller,
        worker_id="runtime-unit-1",
        max_batches=0,
    )

    assert result.scanned_batch_ids == []
    assert result.batch_results == []


def _caller_for(tenant_id: str) -> ReportCallerContext:
    return _caller().model_copy(update={"tenant_id": tenant_id})


def _request_for(portfolio_id: str, tenant_id: str) -> BatchCreateRequest:
    return _request(portfolio_id).model_copy(
        update={
            "source_candidates": [
                _candidate(portfolio_id).model_copy(update={"tenant_id": tenant_id})
            ]
        }
    )


@pytest.mark.asyncio
async def test_runtime_pass_only_scans_batches_of_the_governed_tenant(tmp_path) -> None:
    """A background pass must not advance a batch belonging to another tenant."""

    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    governed = batch_ledger.create_batch(
        request=_request_for("PB_SG_GLOBAL_BAL_001", "tenant-sg"),
        caller_context=_caller_for("tenant-sg"),
        idempotency_key=f"runtime-governed-{uuid4().hex}",
    )
    foreign = batch_ledger.create_batch(
        request=_request_for("PB_UK_GLOBAL_BAL_001", "tenant-uk"),
        caller_context=_caller_for("tenant-uk"),
        idempotency_key=f"runtime-foreign-{uuid4().hex}",
    )

    result = await _runtime(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
    ).run_pass(
        caller_context=_caller_for("tenant-sg"),
        worker_id="runtime-tenant-scope-1",
        max_batches=5,
    )

    assert result.scanned_batch_ids == [governed.batch_id]
    assert foreign.batch_id not in result.scanned_batch_ids
    untouched = batch_ledger.get_batch(foreign.batch_id)
    assert untouched.status == "materialized"
    assert [item.status for item in untouched.items] == ["materialized"]
    assert [item.report_job_id for item in untouched.items] == [None]


@pytest.mark.asyncio
async def test_runtime_pass_does_not_create_report_jobs_under_a_foreign_tenant(tmp_path) -> None:
    """Report jobs derived from a batch must carry the batch tenant, never the worker's."""

    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch_ledger.create_batch(
        request=_request_for("PB_UK_GLOBAL_BAL_001", "tenant-uk"),
        caller_context=_caller_for("tenant-uk"),
        idempotency_key=f"runtime-foreign-only-{uuid4().hex}",
    )

    result = await _runtime(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
    ).run_pass(
        caller_context=_caller_for("tenant-sg"),
        worker_id="runtime-tenant-scope-2",
        max_batches=5,
    )

    assert result.scanned_batch_ids == []
    assert result.dispatched_count == 0
    assert result.executed_count == 0


def test_multi_tenant_pass_derives_context_per_batch_and_ignores_out_of_set(tmp_path):
    """Issue #178 acceptance: an in-set batch progresses under a caller context
    carrying its own batch tenant, an out-of-set batch is untouched (not an
    error), and the scan itself is set-scoped."""

    import asyncio

    from app.report_batch_orchestrator.runtime import ReportBatchRuntime

    class _Ledger:
        def __init__(self):
            self.scanned_with = None
            self.batches = {
                "rbch_in_a": "tenant-a",
                "rbch_in_b": "tenant-b",
            }

        def list_runnable_batch_ids(self, *, tenant_ids, limit=10, now=None):
            self.scanned_with = tuple(tenant_ids)
            # Simulate a stale row sneaking past the predicate to exercise the
            # defence-in-depth validation as well.
            return ["rbch_in_a", "rbch_in_b", "rbch_foreign"]

        def get_batch(self, batch_id):
            from types import SimpleNamespace

            return SimpleNamespace(tenant_id=self.batches.get(batch_id, "tenant-foreign"))

        def batch_pressure_snapshot(self, *, now=None):
            from app.report_batch_orchestrator.models import BatchPressureSnapshot

            return BatchPressureSnapshot()

    class _WorkerSpy:
        def __init__(self):
            self.contexts = []

        async def run_once(self, *, batch_id, caller_context, worker_id, **kwargs):
            from app.report_batch_orchestrator.worker import BatchWorkerRunResult

            self.contexts.append((batch_id, caller_context.tenant_id))
            return BatchWorkerRunResult(
                batch_id=batch_id,
                batch_status_before="materialized",
                batch_status_after="running",
                recovered_count=0,
                leased_count=0,
                dispatched_count=1,
                executed_count=1,
            )

    ledger = _Ledger()
    worker = _WorkerSpy()
    runtime = ReportBatchRuntime(batch_ledger=ledger, worker=worker)
    base_context = _caller()

    result = asyncio.run(
        runtime.run_pass(
            caller_context=base_context,
            worker_id="worker-multi",
            authorized_tenant_ids=("tenant-a", "tenant-b"),
            max_batches=10,
        )
    )

    assert ledger.scanned_with == ("tenant-a", "tenant-b")
    # Each mutation ran under the batch's own tenant - never the base context's.
    assert worker.contexts == [
        ("rbch_in_a", "tenant-a"),
        ("rbch_in_b", "tenant-b"),
    ]
    # The foreign batch was neither advanced nor an error condition of the pass.
    assert len(result.batch_results) == 2
