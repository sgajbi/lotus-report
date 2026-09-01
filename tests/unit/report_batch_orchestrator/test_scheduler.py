from __future__ import annotations

import pytest

from app.config import Settings
from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.scheduler import (
    BatchScheduleConfigError,
    BatchScheduleDefinition,
    BatchSchedulerConfig,
    ReportBatchScheduler,
    batch_scheduler_config_from_settings,
)
from app.reporting_jobs.models import ReportCallerContext


class _PortfolioSource:
    def __init__(
        self,
        payloads: dict[str, tuple[int, dict[str, object]]],
        *,
        list_payload: tuple[int, dict[str, object]] | None = None,
    ) -> None:
        self.payloads = payloads
        self.list_payload = list_payload or (200, {"portfolios": []})
        self.calls: list[tuple[str, str | None]] = []
        self.list_calls: list[str | None] = []

    async def get_portfolio_detail(
        self,
        portfolio_id: str,
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        self.calls.append((portfolio_id, correlation_id))
        return self.payloads.get(portfolio_id, (404, {}))

    async def list_portfolios(
        self,
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        self.list_calls.append(correlation_id)
        return self.list_payload


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


def test_batch_scheduler_config_parses_manifest_schedule_json() -> None:
    source = Settings(
        _env_file=None,
        REPORT_BATCH_SCHEDULES_JSON=(
            '[{"schedule_id":"monthly-manifest","selector_mode":"batch_manifest",'
            '"frequency":"monthly","as_of_date":"2026-04-30",'
            '"manifest_source":"ops-batch-2026-04","manifest_entries":['
            '{"portfolio_id":"P1","source_system":"lotus-operations",'
            '"source_object":"BatchManifest"}]}]'
        ),
    )

    config = batch_scheduler_config_from_settings(source)

    assert config.schedules[0].selector_mode == "batch_manifest"
    assert config.schedules[0].manifest_entries[0].portfolio_id == "P1"


@pytest.mark.parametrize(
    "overrides,expected_code",
    [
        ({"requested_output_formats": ["xlsx"]}, "unsupported_report_output_format"),
        ({"options": {"sections": ["CLIENT_STATEMENT"]}}, "unsupported_report_section"),
        ({"options": {"template_id": "unapproved-template"}}, "unsupported_report_configuration"),
    ],
)
def test_batch_schedule_rejects_configuration_outside_the_published_catalogue(
    overrides: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(ValueError, match=expected_code):
        _schedule(**overrides)


@pytest.mark.parametrize("raw", ["{}", "{"])
def test_batch_scheduler_config_rejects_invalid_json(raw: str) -> None:
    source = Settings(_env_file=None, REPORT_BATCH_SCHEDULES_JSON=raw)

    with pytest.raises(BatchScheduleConfigError):
        batch_scheduler_config_from_settings(source)


@pytest.mark.parametrize(
    "raw",
    [
        (
            '[{"schedule_id":"missing-ids","selector_mode":"explicit_portfolio_list",'
            '"frequency":"monthly","as_of_date":"2026-04-30"}]'
        ),
        (
            '[{"schedule_id":"unsupported-subset","selector_mode":"selected_subset",'
            '"frequency":"monthly","as_of_date":"2026-04-30"}]'
        ),
        (
            '[{"schedule_id":"missing-manifest","selector_mode":"batch_manifest",'
            '"frequency":"monthly","as_of_date":"2026-04-30"}]'
        ),
        (
            '[{"schedule_id":"duplicate-manifest","selector_mode":"batch_manifest",'
            '"frequency":"monthly","as_of_date":"2026-04-30","manifest_entries":['
            '{"portfolio_id":"P1"},{"portfolio_id":"P1"}]}]'
        ),
    ],
)
def test_batch_scheduler_config_rejects_unsupported_schedule_sources(raw: str) -> None:
    source = Settings(_env_file=None, REPORT_BATCH_SCHEDULES_JSON=raw)

    with pytest.raises(BatchScheduleConfigError):
        batch_scheduler_config_from_settings(source)


async def test_scheduler_refuses_broad_discovery_it_cannot_attribute(tmp_path) -> None:
    """A scheduled all-active pass would ask lotus-core for every active
    portfolio and then label the answer with this scheduler's CONFIGURED
    tenant - configuration presented as evidence (issue #177). Report refuses
    rather than stamping, and refuses BEFORE asking: the unqualified discovery
    call is never made.
    """

    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {},
        list_payload=(
            200,
            {
                "portfolios": [
                    {"portfolio_id": "PB_SG_GLOBAL_BAL_001", "status": "active"},
                    {"portfolio_id": "PB_SG_GLOBAL_BAL_002", "status": "active"},
                ]
            },
        ),
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    result = await scheduler.run_due_schedules(
        config=_config(_schedule(selector_mode="all_active_portfolios", portfolio_ids=[])),
        caller_context=_caller_context(),
    )

    assert result.materialized == ()
    assert result.refused_schedule_ids == ("monthly-sg-global-bal",)
    # A refusal is not a skip: collapsing them would hide a governance stop
    # inside ordinary quiet.
    assert result.skipped_schedule_ids == ()
    # Refused before discovery, so no portfolio was ever fetched to be stamped.
    assert source.list_calls == []
    assert source.calls == []


async def test_a_refused_schedule_creates_no_durable_batch(tmp_path) -> None:
    """The invariant #177 exists for: no batch attributed to a tenant that was
    never proven to own its portfolios."""

    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {},
        list_payload=(200, {"portfolios": [{"portfolio_id": "PB_X", "status": "active"}]}),
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    await scheduler.run_due_schedules(
        config=_config(_schedule(selector_mode="all_active_portfolios", portfolio_ids=[])),
        caller_context=_caller_context(),
    )

    # Nothing durable exists to be attributed: the ledger holds no runnable
    # batch for the refused pass.
    assert ledger.list_runnable_batch_ids(tenant_ids=["tenant-sg"], limit=10) == []


async def test_enumerated_schedules_are_unaffected_by_the_refusal(tmp_path) -> None:
    """Only broad discovery is refused. An explicitly enumerated schedule
    carries a claim someone made - the Gateway trusted-scope front door for
    stored schedules, a deployment operator for configured ones - rather than
    one Report invented, so it still materializes."""

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
        config=_config(_schedule(portfolio_ids=["PB_SG_GLOBAL_BAL_001"])),
        caller_context=_caller_context(),
    )

    assert result.refused_schedule_ids == ()
    assert len(result.materialized) == 1


async def test_scheduler_materializes_manifest_schedule_with_provenance(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {
            "PB_SG_GLOBAL_BAL_001": (
                200,
                {"portfolio_id": "PB_SG_GLOBAL_BAL_001", "status": "active"},
            ),
            "PB_SG_GLOBAL_BAL_002": (
                200,
                {"portfolio_id": "PB_SG_GLOBAL_BAL_002", "status": "active"},
            ),
        }
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    result = await scheduler.run_due_schedules(
        config=_config(
            _schedule(
                selector_mode="batch_manifest",
                portfolio_ids=[],
                manifest_source="ops-manifest-apac-monthly",
                manifest_version="2026-04",
                manifest_entries=[
                    {
                        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                        "source_system": "lotus-operations",
                        "source_object": "BatchManifest",
                    },
                    {
                        "portfolio_id": "PB_SG_GLOBAL_BAL_002",
                        "source_system": "lotus-operations",
                        "source_object": "BatchManifest",
                    },
                ],
            )
        ),
        caller_context=_caller_context(),
    )

    batch = ledger.get_batch(result.materialized[0].batch_id)
    assert batch.selector_mode == "batch_manifest"
    assert batch.materialized_portfolio_ids == [
        "PB_SG_GLOBAL_BAL_001",
        "PB_SG_GLOBAL_BAL_002",
    ]
    assert batch.options["batch_manifest_source"] == "ops-manifest-apac-monthly"
    assert batch.options["batch_manifest_version"] == "2026-04"
    assert len(batch.options["batch_manifest_hash"]) == 32


async def test_scheduler_preserves_supplied_manifest_hash(tmp_path) -> None:
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
            _schedule(
                selector_mode="batch_manifest",
                portfolio_ids=[],
                manifest_hash="operator-signed-hash-001",
                manifest_entries=[{"portfolio_id": "PB_SG_GLOBAL_BAL_001"}],
            )
        ),
        caller_context=_caller_context(),
    )

    batch = ledger.get_batch(result.materialized[0].batch_id)
    assert batch.options["batch_manifest_hash"] == "operator-signed-hash-001"


async def test_scheduler_skips_manifest_schedule_without_verified_candidates(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {
            "PB_SG_GLOBAL_BAL_001": (
                200,
                {"portfolio_id": "PB_SG_DIFFERENT_001", "status": "active"},
            ),
            "PB_SG_GLOBAL_BAL_002": (503, {}),
        }
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    result = await scheduler.run_due_schedules(
        config=_config(
            _schedule(
                selector_mode="batch_manifest",
                portfolio_ids=[],
                manifest_entries=[
                    {"portfolio_id": "PB_SG_GLOBAL_BAL_001"},
                    {"portfolio_id": "PB_SG_GLOBAL_BAL_002"},
                ],
            )
        ),
        caller_context=_caller_context(),
    )

    assert result.materialized == ()
    assert result.skipped_schedule_ids == ("monthly-sg-global-bal",)


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


async def test_scheduler_skips_mismatched_portfolio_payloads(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")
    source = _PortfolioSource(
        {
            "PB_SG_GLOBAL_BAL_001": (
                200,
                {"portfolio_id": "PB_SG_DIFFERENT_001", "status": "active"},
            )
        }
    )
    scheduler = ReportBatchScheduler(batch_ledger=ledger, portfolio_source=source)

    result = await scheduler.run_due_schedules(
        config=_config(_schedule()),
        caller_context=_caller_context(),
    )

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
