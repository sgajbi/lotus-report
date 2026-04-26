from __future__ import annotations

from app.config import Settings
from app.report_batch_orchestrator.models import BatchDispatchPolicy
from app.report_batch_orchestrator.process import (
    BatchWorkerProcess,
    BatchWorkerProcessConfig,
    batch_worker_caller_context,
    batch_worker_config_from_settings,
)
from app.report_batch_orchestrator.runtime import BatchRuntimePassResult
from app.reporting_jobs.models import ReportCallerContext


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_pass(
        self,
        *,
        caller_context: ReportCallerContext,
        worker_id: str,
        max_batches: int = 5,
        dispatch_policy: BatchDispatchPolicy | None = None,
        recover_expired_leases: bool = True,
    ) -> BatchRuntimePassResult:
        self.calls.append(
            {
                "caller_context": caller_context,
                "worker_id": worker_id,
                "max_batches": max_batches,
                "dispatch_policy": dispatch_policy,
                "recover_expired_leases": recover_expired_leases,
            }
        )
        return BatchRuntimePassResult(
            worker_id=worker_id,
            scanned_batch_ids=[f"rbch_{len(self.calls)}"],
            dispatched_count=1,
            executed_count=1,
        )


def _config() -> BatchWorkerProcessConfig:
    return BatchWorkerProcessConfig(
        worker_id="worker-unit-1",
        interval_seconds=0.1,
        max_batches_per_pass=3,
        caller_context_tenant_id="tenant-sg",
        caller_context_region="APAC",
        caller_context_booking_center_code="SG",
        caller_context_role="system",
        dispatch_policy=BatchDispatchPolicy(max_active_batches=7, max_active_items=11),
    )


def test_batch_worker_config_from_settings_maps_runtime_policy() -> None:
    source = Settings(
        _env_file=None,
        REPORT_BATCH_WORKER_ID="worker-config-1",
        REPORT_BATCH_WORKER_INTERVAL_SECONDS=2.5,
        REPORT_BATCH_WORKER_MAX_BATCHES_PER_PASS=13,
        REPORT_BATCH_WORKER_TENANT_ID="tenant-private-bank",
        REPORT_BATCH_WORKER_REGION="EMEA",
        REPORT_BATCH_WORKER_BOOKING_CENTER_CODE="CH",
        REPORT_BATCH_WORKER_ROLE="operations",
        REPORT_BATCH_WORKER_MAX_ACTIVE_BATCHES=17,
        REPORT_BATCH_WORKER_MAX_ACTIVE_ITEMS=19,
        REPORT_BATCH_WORKER_MAX_ACTIVE_UPSTREAM_JOBS=23,
        REPORT_BATCH_WORKER_MAX_ACTIVE_RENDER_JOBS=29,
        REPORT_BATCH_WORKER_MAX_ACTIVE_ARCHIVE_JOBS=31,
        REPORT_BATCH_WORKER_LEASE_SECONDS=600,
    )

    config = batch_worker_config_from_settings(source)

    assert config.worker_id == "worker-config-1"
    assert config.interval_seconds == 2.5
    assert config.max_batches_per_pass == 13
    assert config.caller_context_tenant_id == "tenant-private-bank"
    assert config.caller_context_region == "EMEA"
    assert config.caller_context_booking_center_code == "CH"
    assert config.caller_context_role == "operations"
    assert config.dispatch_policy.max_active_batches == 17
    assert config.dispatch_policy.max_active_items == 19
    assert config.dispatch_policy.max_active_upstream_jobs == 23
    assert config.dispatch_policy.max_active_render_jobs == 29
    assert config.dispatch_policy.max_active_archive_jobs == 31
    assert config.dispatch_policy.lease_seconds == 600


def test_batch_worker_caller_context_uses_worker_identity_and_unique_trace() -> None:
    first = batch_worker_caller_context(_config(), pass_sequence=1)
    second = batch_worker_caller_context(_config(), pass_sequence=2)

    assert first.triggered_by == "worker-unit-1"
    assert first.caller_application == "lotus-report-batch-worker"
    assert first.tenant_id == "tenant-sg"
    assert first.region == "APAC"
    assert first.booking_center_code == "SG"
    assert first.role == "system"
    assert first.correlation_id.startswith("corr-batch-worker-1-")
    assert second.correlation_id.startswith("corr-batch-worker-2-")
    assert first.trace_id != second.trace_id


async def test_batch_worker_process_runs_bounded_iterations_and_sleeps() -> None:
    runtime = _Runtime()
    sleep_calls: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    process = BatchWorkerProcess(runtime=runtime, config=_config(), sleep=_sleep)

    await process.run(max_iterations=2)

    assert len(runtime.calls) == 2
    assert sleep_calls == [0.1]
    assert [call["worker_id"] for call in runtime.calls] == ["worker-unit-1", "worker-unit-1"]
    assert [call["max_batches"] for call in runtime.calls] == [3, 3]
    assert all(call["recover_expired_leases"] is True for call in runtime.calls)
    assert all(isinstance(call["dispatch_policy"], BatchDispatchPolicy) for call in runtime.calls)


async def test_batch_worker_process_can_stop_after_current_pass() -> None:
    runtime = _Runtime()

    async def _sleep(_seconds: float) -> None:
        process.stop()

    process = BatchWorkerProcess(runtime=runtime, config=_config(), sleep=_sleep)

    await process.run()

    assert len(runtime.calls) == 1
