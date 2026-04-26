from __future__ import annotations

import pytest

from app.config import Settings
from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.scheduler import (
    BatchScheduleConfigError,
    BatchScheduleDefinition,
    BatchSchedulerConfig,
    ReportBatchScheduler,
    batch_scheduler_caller_context,
    batch_scheduler_config_from_settings,
)
from app.reporting_jobs.models import ReportCallerContext


class _PortfolioSource:
    def __init__(self, payloads: dict[str, tuple[int, dict[str, object]]]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str | None]] = []

    async def get_portfolio_detail(
        self,
        portfolio_id: str,
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        self.calls.append((portfolio_id, correlation_id))
        return self.payloads.get(portfolio_id, (404, {}))


def _caller_context() -> ReportCallerContext:
    return ReportCallerContext(
        trigger_type="system",
        triggered_by="scheduler",
        caller_application="lotus-report-batch-scheduler",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="system",
        correlation_id="corr-scheduler-unit",
        trace_id="trace-scheduler-unit",
    )


def _config(*schedules: BatchScheduleDefinition) -> BatchSchedulerConfig:
    return BatchSchedulerConfig(
        scheduler_id="scheduler-unit-1",
        interval_seconds=1.0,
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="system",
        schedules=tuple(schedules),
    )


def _schedule(**overrides: object) -> BatchScheduleDefinition:
    values: dict[str, object] = {
        "schedule_id": "monthly-sg-global-bal",
        "frequency": "monthly",
        "as_of_date": "2026-04-22",
        "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
        "requested_output_formats": ["pdf"],
        "reporting_currency": "USD",
        "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
        "max_batch_size": 10,
    }
    values.update(overrides)
    return BatchScheduleDefinition.model_validate(values)


def test_batch_scheduler_config_from_settings_parses_schedule_json() -> None:
    source = Settings(
        _env_file=None,
        REPORT_BATCH_SCHEDULER_ID="scheduler-config-1",
        REPORT_BATCH_SCHEDULER_INTERVAL_SECONDS=2.5,
        REPORT_BATCH_SCHEDULER_TENANT_ID="tenant-private-bank",
        REPORT_BATCH_SCHEDULER_REGION="EMEA",
        REPORT_BATCH_SCHEDULER_BOOKING_CENTER_CODE="CH",
        REPORT_BATCH_SCHEDULER_ROLE="operations",
        REPORT_BATCH_SCHEDULES_JSON=(
            '[{"schedule_id":"monthly-emea","frequency":"monthly",'
            '"as_of_date":"2026-04-30","portfolio_ids":["P1"]}]'
        ),
    )

    config = batch_scheduler_config_from_settings(source)

    assert config.scheduler_id == "scheduler-config-1"
    assert config.interval_seconds == 2.5
    assert config.tenant_id == "tenant-private-bank"
    assert config.region == "EMEA"
    assert config.booking_center_code == "CH"
    assert config.role == "operations"
    assert len(config.schedules) == 1
    assert config.schedules[0].schedule_id == "monthly-emea"


@pytest.mark.parametrize("raw", ["{}", "{"])
def test_batch_scheduler_config_rejects_invalid_json(raw: str) -> None:
    source = Settings(_env_file=None, REPORT_BATCH_SCHEDULES_JSON=raw)

    with pytest.raises(BatchScheduleConfigError):
        batch_scheduler_config_from_settings(source)


def test_batch_scheduler_caller_context_is_deterministic_for_pass() -> None:
    config = _config(_schedule())

    first = batch_scheduler_caller_context(config, pass_sequence=1)
    second = batch_scheduler_caller_context(config, pass_sequence=1)
    changed = batch_scheduler_caller_context(config, pass_sequence=2)

    assert first == second
    assert first.triggered_by == "scheduler-unit-1"
    assert first.caller_application == "lotus-report-batch-scheduler"
    assert first.correlation_id.startswith("corr-batch-scheduler-1-")
    assert changed.correlation_id.startswith("corr-batch-scheduler-2-")


async def test_scheduler_materializes_due_schedule_from_core_candidates(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {
            "PB_SG_GLOBAL_BAL_001": (
                200,
                {"portfolio_id": "PB_SG_GLOBAL_BAL_001", "status": "active"},
            )
        }
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    result = await scheduler.run_due_schedules(
        config=_config(_schedule()),
        caller_context=_caller_context(),
    )

    assert result.attempted_count == 1
    assert result.skipped_schedule_ids == ()
    assert len(result.materialized) == 1
    materialized = result.materialized[0]
    batch = ledger.get_batch(materialized.batch_id)
    assert batch.status == "materialized"
    assert batch.materialized_portfolio_ids == ["PB_SG_GLOBAL_BAL_001"]
    assert batch.options["batch_schedule_id"] == "monthly-sg-global-bal"
    assert batch.options["batch_frequency"] == "monthly"
    assert source.calls == [("PB_SG_GLOBAL_BAL_001", "corr-scheduler-unit")]


async def test_scheduler_is_idempotent_for_same_schedule(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {
            "PB_SG_GLOBAL_BAL_001": (
                200,
                {"portfolio_id": "PB_SG_GLOBAL_BAL_001", "status": "active"},
            )
        }
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)
    config = _config(_schedule())

    first = await scheduler.run_due_schedules(config=config, caller_context=_caller_context())
    second = await scheduler.run_due_schedules(config=config, caller_context=_caller_context())

    assert first.materialized[0].batch_id == second.materialized[0].batch_id
    assert first.materialized[0].idempotency_key == second.materialized[0].idempotency_key


async def test_scheduler_skips_missing_portfolios(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource({})
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    result = await scheduler.run_due_schedules(
        config=_config(_schedule()),
        caller_context=_caller_context(),
    )

    assert result.attempted_count == 1
    assert result.materialized == ()
    assert result.skipped_schedule_ids == ("monthly-sg-global-bal",)


async def test_scheduler_rejects_inactive_portfolios(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {
            "PB_SG_GLOBAL_BAL_001": (
                200,
                {"portfolio_id": "PB_SG_GLOBAL_BAL_001", "status": "closed"},
            )
        }
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    with pytest.raises(ValueError, match="inactive_portfolio"):
        await scheduler.run_due_schedules(
            config=_config(_schedule()),
            caller_context=_caller_context(),
        )


async def test_scheduler_keeps_distinct_schedules_from_colliding(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {
            "PB_SG_GLOBAL_BAL_001": (
                200,
                {"portfolio_id": "PB_SG_GLOBAL_BAL_001", "status": "active"},
            )
        }
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    result = await scheduler.run_due_schedules(
        config=_config(
            _schedule(schedule_id="monthly-a", options={"sections": ["OVERVIEW"]}),
            _schedule(schedule_id="monthly-b", options={"sections": ["PERFORMANCE"]}),
        ),
        caller_context=_caller_context(),
    )

    assert len(result.materialized) == 2
    assert result.materialized[0].batch_id != result.materialized[1].batch_id
    assert result.materialized[0].idempotency_key != result.materialized[1].idempotency_key
