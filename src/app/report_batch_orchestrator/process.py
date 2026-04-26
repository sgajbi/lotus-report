from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol
from uuid import uuid4

from app.config import Settings, settings
from app.observability import (
    correlation_id_var,
    request_id_var,
    setup_logging,
    trace_id_var,
)
from app.report_batch_orchestrator.models import BatchDispatchPolicy
from app.report_batch_orchestrator.runtime import BatchRuntimePassResult, ReportBatchRuntime
from app.report_batch_orchestrator.service import get_report_batch_runtime
from app.reporting_jobs.models import ReportCallerContext

Sleep = Callable[[float], Awaitable[None]]


class BatchRuntimeProcess(Protocol):
    async def run_pass(
        self,
        *,
        caller_context: ReportCallerContext,
        worker_id: str,
        max_batches: int = 5,
        dispatch_policy: BatchDispatchPolicy | None = None,
        recover_expired_leases: bool = True,
    ) -> BatchRuntimePassResult: ...


@dataclass(frozen=True)
class BatchWorkerProcessConfig:
    worker_id: str
    interval_seconds: float
    max_batches_per_pass: int
    caller_context_tenant_id: str
    caller_context_region: str
    caller_context_booking_center_code: str | None
    caller_context_role: str
    dispatch_policy: BatchDispatchPolicy


def batch_worker_config_from_settings(source: Settings = settings) -> BatchWorkerProcessConfig:
    return BatchWorkerProcessConfig(
        worker_id=source.batch_worker_id,
        interval_seconds=source.batch_worker_interval_seconds,
        max_batches_per_pass=source.batch_worker_max_batches_per_pass,
        caller_context_tenant_id=source.batch_worker_tenant_id,
        caller_context_region=source.batch_worker_region,
        caller_context_booking_center_code=source.batch_worker_booking_center_code,
        caller_context_role=source.batch_worker_role,
        dispatch_policy=BatchDispatchPolicy(
            max_active_batches=source.batch_worker_max_active_batches,
            max_active_items=source.batch_worker_max_active_items,
            max_active_upstream_jobs=source.batch_worker_max_active_upstream_jobs,
            max_active_render_jobs=source.batch_worker_max_active_render_jobs,
            max_active_archive_jobs=source.batch_worker_max_active_archive_jobs,
            lease_seconds=source.batch_worker_lease_seconds,
        ),
    )


def batch_worker_caller_context(
    config: BatchWorkerProcessConfig,
    *,
    pass_sequence: int,
) -> ReportCallerContext:
    suffix = uuid4().hex
    return ReportCallerContext(
        triggered_by=config.worker_id,
        caller_application="lotus-report-batch-worker",
        tenant_id=config.caller_context_tenant_id,
        region=config.caller_context_region,
        booking_center_code=config.caller_context_booking_center_code,
        role=config.caller_context_role,
        correlation_id=f"corr-batch-worker-{pass_sequence}-{suffix[:12]}",
        trace_id=suffix,
    )


class BatchWorkerProcess:
    def __init__(
        self,
        *,
        runtime: BatchRuntimeProcess,
        config: BatchWorkerProcessConfig,
        sleep: Sleep = asyncio.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self._runtime = runtime
        self._config = config
        self._sleep = sleep
        self._logger = logger or logging.getLogger("report_batch_worker")
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True

    async def run(self, *, max_iterations: int | None = None) -> None:
        iteration = 0
        while not self._stopping:
            iteration += 1
            context = batch_worker_caller_context(self._config, pass_sequence=iteration)
            corr_token = correlation_id_var.set(context.correlation_id)
            req_token = request_id_var.set(f"batch_worker_pass_{iteration}")
            trace_token = trace_id_var.set(context.trace_id)
            try:
                result = await self._runtime.run_pass(
                    caller_context=context,
                    worker_id=self._config.worker_id,
                    max_batches=self._config.max_batches_per_pass,
                    dispatch_policy=self._config.dispatch_policy,
                    recover_expired_leases=True,
                )
                self._log_pass_result(iteration=iteration, result=result)
            finally:
                correlation_id_var.reset(corr_token)
                request_id_var.reset(req_token)
                trace_id_var.reset(trace_token)

            if max_iterations is not None and iteration >= max_iterations:
                break
            await self._sleep(self._config.interval_seconds)

    def _log_pass_result(
        self,
        *,
        iteration: int,
        result: BatchRuntimePassResult,
    ) -> None:
        self._logger.info(
            "batch_worker.pass_completed",
            extra={
                "extra_fields": {
                    "worker_id": self._config.worker_id,
                    "iteration": iteration,
                    "scanned_batch_count": len(result.scanned_batch_ids),
                    "scanned_batch_ids": result.scanned_batch_ids,
                    "batch_result_count": len(result.batch_results),
                    "recovered_count": result.recovered_count,
                    "leased_count": result.leased_count,
                    "dispatched_count": result.dispatched_count,
                    "executed_count": result.executed_count,
                    "back_pressure_stopped": result.back_pressure_stopped,
                    "back_pressure_reasons": result.back_pressure_reasons,
                }
            },
        )


async def run_batch_worker_process(
    *,
    runtime: ReportBatchRuntime | None = None,
    source_settings: Settings = settings,
    max_iterations: int | None = None,
) -> None:
    process = BatchWorkerProcess(
        runtime=runtime or get_report_batch_runtime(),
        config=batch_worker_config_from_settings(source_settings),
    )
    await process.run(max_iterations=max_iterations)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the lotus-report RFC-0104 batch worker.")
    parser.add_argument("--once", action="store_true", help="Run one worker pass and exit.")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Run a bounded number of worker passes and exit.",
    )
    args = parser.parse_args()
    setup_logging()
    max_iterations = 1 if args.once else args.max_iterations
    asyncio.run(run_batch_worker_process(max_iterations=max_iterations))


if __name__ == "__main__":
    main()
