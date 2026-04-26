from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from app.config import Settings, settings
from app.observability import (
    correlation_id_var,
    request_id_var,
    setup_logging,
    trace_id_var,
)
from app.report_batch_orchestrator.scheduler import (
    BatchSchedulerConfig,
    BatchSchedulerRunResult,
    batch_scheduler_caller_context,
    batch_scheduler_config_from_settings,
)
from app.report_batch_orchestrator.service import get_report_batch_scheduler
from app.reporting_jobs.models import ReportCallerContext

Sleep = Callable[[float], Awaitable[None]]


class BatchSchedulerRuntime(Protocol):
    async def run_due_schedules(
        self,
        *,
        config: BatchSchedulerConfig,
        caller_context: ReportCallerContext,
    ) -> BatchSchedulerRunResult: ...


@dataclass(frozen=True)
class BatchSchedulerProcessConfig:
    scheduler_config: BatchSchedulerConfig

    @property
    def scheduler_id(self) -> str:
        return self.scheduler_config.scheduler_id

    @property
    def interval_seconds(self) -> float:
        return self.scheduler_config.interval_seconds


def batch_scheduler_process_config_from_settings(
    source: Settings = settings,
) -> BatchSchedulerProcessConfig:
    return BatchSchedulerProcessConfig(
        scheduler_config=batch_scheduler_config_from_settings(source)
    )


class BatchSchedulerProcess:
    def __init__(
        self,
        *,
        scheduler: BatchSchedulerRuntime,
        config: BatchSchedulerProcessConfig,
        sleep: Sleep = asyncio.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._config = config
        self._sleep = sleep
        self._logger = logger or logging.getLogger("report_batch_scheduler")
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True

    async def run(self, *, max_iterations: int | None = None) -> None:
        iteration = 0
        while not self._stopping:
            iteration += 1
            context = batch_scheduler_caller_context(
                self._config.scheduler_config,
                pass_sequence=iteration,
            )
            corr_token = correlation_id_var.set(context.correlation_id)
            req_token = request_id_var.set(f"batch_scheduler_pass_{iteration}")
            trace_token = trace_id_var.set(context.trace_id)
            try:
                result = await self._scheduler.run_due_schedules(
                    config=self._config.scheduler_config,
                    caller_context=context,
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
        result: BatchSchedulerRunResult,
    ) -> None:
        self._logger.info(
            "batch_scheduler.pass_completed",
            extra={
                "extra_fields": {
                    "scheduler_id": self._config.scheduler_id,
                    "iteration": iteration,
                    "attempted_count": result.attempted_count,
                    "materialized_count": len(result.materialized),
                    "materialized_batch_ids": [item.batch_id for item in result.materialized],
                    "skipped_schedule_ids": list(result.skipped_schedule_ids),
                }
            },
        )


async def run_batch_scheduler_process(
    *,
    scheduler: BatchSchedulerRuntime | None = None,
    source_settings: Settings = settings,
    max_iterations: int | None = None,
) -> None:
    process = BatchSchedulerProcess(
        scheduler=scheduler or get_report_batch_scheduler(),
        config=batch_scheduler_process_config_from_settings(source_settings),
    )
    await process.run(max_iterations=max_iterations)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the lotus-report RFC-0104 batch scheduler.")
    parser.add_argument("--once", action="store_true", help="Run one scheduler pass and exit.")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Run a bounded number of scheduler passes and exit.",
    )
    args = parser.parse_args()
    setup_logging()
    max_iterations = 1 if args.once else args.max_iterations
    asyncio.run(run_batch_scheduler_process(max_iterations=max_iterations))


if __name__ == "__main__":  # pragma: no cover
    main()
