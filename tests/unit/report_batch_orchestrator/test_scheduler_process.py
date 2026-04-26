from __future__ import annotations

import sys

from app.config import Settings
from app.report_batch_orchestrator import scheduler_process as process_module
from app.report_batch_orchestrator.scheduler import (
    BatchSchedulerConfig,
    BatchSchedulerMaterialization,
    BatchSchedulerRunResult,
)
from app.report_batch_orchestrator.scheduler_process import (
    BatchSchedulerProcess,
    batch_scheduler_process_config_from_settings,
)
from app.reporting_jobs.models import ReportCallerContext


class _Scheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_due_schedules(
        self,
        *,
        config: BatchSchedulerConfig,
        caller_context: ReportCallerContext,
    ) -> BatchSchedulerRunResult:
        self.calls.append({"config": config, "caller_context": caller_context})
        return BatchSchedulerRunResult(
            scheduler_id=config.scheduler_id,
            attempted_count=len(config.schedules),
            materialized=(
                BatchSchedulerMaterialization(
                    schedule_id="monthly-sg-global-bal",
                    batch_id=f"rbch_{len(self.calls)}",
                    idempotency_key=f"scheduled-batch-{len(self.calls)}",
                    item_count=1,
                    status="materialized",
                ),
            ),
            skipped_schedule_ids=(),
        )


def test_batch_scheduler_process_config_from_settings() -> None:
    source = Settings(
        _env_file=None,
        REPORT_BATCH_SCHEDULER_ID="scheduler-process-1",
        REPORT_BATCH_SCHEDULER_INTERVAL_SECONDS=3.0,
        REPORT_BATCH_SCHEDULES_JSON=(
            '[{"schedule_id":"monthly-sg","frequency":"monthly",'
            '"as_of_date":"2026-04-22","portfolio_ids":["P1"]}]'
        ),
    )

    config = batch_scheduler_process_config_from_settings(source)

    assert config.scheduler_id == "scheduler-process-1"
    assert config.interval_seconds == 3.0
    assert len(config.scheduler_config.schedules) == 1


async def test_batch_scheduler_process_runs_bounded_iterations_and_sleeps() -> None:
    scheduler = _Scheduler()
    sleep_calls: list[float] = []
    config = batch_scheduler_process_config_from_settings(
        Settings(
            _env_file=None,
            REPORT_BATCH_SCHEDULER_INTERVAL_SECONDS=0.1,
            REPORT_BATCH_SCHEDULES_JSON=(
                '[{"schedule_id":"monthly-sg","frequency":"monthly",'
                '"as_of_date":"2026-04-22","portfolio_ids":["P1"]}]'
            ),
        )
    )

    async def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    process = BatchSchedulerProcess(scheduler=scheduler, config=config, sleep=_sleep)

    await process.run(max_iterations=2)

    assert len(scheduler.calls) == 2
    assert sleep_calls == [0.1]
    assert all(isinstance(call["caller_context"], ReportCallerContext) for call in scheduler.calls)


async def test_batch_scheduler_process_can_stop_after_current_pass() -> None:
    scheduler = _Scheduler()
    config = batch_scheduler_process_config_from_settings(
        Settings(
            _env_file=None,
            REPORT_BATCH_SCHEDULES_JSON=(
                '[{"schedule_id":"monthly-sg","frequency":"monthly",'
                '"as_of_date":"2026-04-22","portfolio_ids":["P1"]}]'
            ),
        )
    )

    async def _sleep(_seconds: float) -> None:
        process.stop()

    process = BatchSchedulerProcess(scheduler=scheduler, config=config, sleep=_sleep)

    await process.run()

    assert len(scheduler.calls) == 1


async def test_run_batch_scheduler_process_uses_supplied_runtime() -> None:
    scheduler = _Scheduler()
    source = Settings(
        _env_file=None,
        REPORT_BATCH_SCHEDULER_ID="scheduler-main-1",
        REPORT_BATCH_SCHEDULES_JSON=(
            '[{"schedule_id":"monthly-sg","frequency":"monthly",'
            '"as_of_date":"2026-04-22","portfolio_ids":["P1"]}]'
        ),
    )

    await process_module.run_batch_scheduler_process(
        scheduler=scheduler,
        source_settings=source,
        max_iterations=1,
    )

    assert len(scheduler.calls) == 1
    call_config = scheduler.calls[0]["config"]
    assert isinstance(call_config, BatchSchedulerConfig)
    assert call_config.scheduler_id == "scheduler-main-1"


def test_main_maps_once_flag_to_single_iteration(monkeypatch) -> None:
    calls: list[int | None] = []

    async def _run_batch_scheduler_process(*, max_iterations: int | None = None) -> None:
        calls.append(max_iterations)

    monkeypatch.setattr(sys, "argv", ["scheduler_process.py", "--once"])
    monkeypatch.setattr(process_module, "setup_logging", lambda: None)
    monkeypatch.setattr(
        process_module,
        "run_batch_scheduler_process",
        _run_batch_scheduler_process,
    )

    process_module.main()

    assert calls == [1]


def test_main_accepts_explicit_max_iterations(monkeypatch) -> None:
    calls: list[int | None] = []

    async def _run_batch_scheduler_process(*, max_iterations: int | None = None) -> None:
        calls.append(max_iterations)

    monkeypatch.setattr(sys, "argv", ["scheduler_process.py", "--max-iterations", "3"])
    monkeypatch.setattr(process_module, "setup_logging", lambda: None)
    monkeypatch.setattr(
        process_module,
        "run_batch_scheduler_process",
        _run_batch_scheduler_process,
    )

    process_module.main()

    assert calls == [3]
