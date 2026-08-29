"""Stored recurring-schedule definitions: cadence math, governance, and audit (issue #167)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.schedule_definitions import (
    BatchScheduleDefinitionCreateRequest,
    BatchScheduleDefinitionUpdateRequest,
    ScheduleDefinitionError,
    ScheduleDefinitionService,
    due_as_of_date,
    month_end,
    next_run_at,
    quarter_end,
    stored_schedule_to_definition,
)
from app.reporting_jobs.models import ReportCallerContext

NOW = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def _caller(tenant_id: str = "tenant-sg", region: str = "APAC"):
    return ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id=tenant_id,
        region=region,
        booking_center_code="SG",
        role="advisor",
        correlation_id="corr-schedule-1",
        trace_id="trace-schedule-1",
    )


def _service(tmp_path: Path) -> ScheduleDefinitionService:
    return ScheduleDefinitionService(ReportBatchLedger(tmp_path / "schedules.sqlite3"))


def _create_request(**overrides) -> BatchScheduleDefinitionCreateRequest:
    payload = {
        "cadence": "quarter_end",
        "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
        "requested_output_formats": ["pdf"],
        "reporting_currency": "USD",
        "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
    }
    payload.update(overrides)
    return BatchScheduleDefinitionCreateRequest(**payload)


def test_month_and_quarter_ends_handle_boundaries() -> None:
    assert month_end(date(2026, 2, 1)) == date(2026, 2, 28)
    assert month_end(date(2028, 2, 15)) == date(2028, 2, 29)
    assert month_end(date(2026, 12, 31)) == date(2026, 12, 31)
    assert quarter_end(date(2026, 1, 1)) == date(2026, 3, 31)
    assert quarter_end(date(2026, 8, 29)) == date(2026, 9, 30)
    assert quarter_end(date(2026, 12, 31)) == date(2026, 12, 31)


def test_a_new_schedule_never_backfills_periods_before_its_creation() -> None:
    created = date(2026, 2, 10)
    # The previous quarter end (Dec 31) predates creation - nothing is due.
    assert due_as_of_date("quarter_end", today=date(2026, 2, 20), created_on=created) is None
    # From the first quarter end after creation it is due.
    assert due_as_of_date("quarter_end", today=date(2026, 3, 31), created_on=created) == (
        date(2026, 3, 31)
    )
    # And remains the due cycle until the next period boundary.
    assert due_as_of_date("quarter_end", today=date(2026, 4, 2), created_on=created) == (
        date(2026, 3, 31)
    )


def test_next_run_at_reports_the_upcoming_cycle_including_year_rollover() -> None:
    created = date(2026, 8, 29)
    assert next_run_at("quarter_end", today=created, created_on=created) == date(2026, 9, 30)
    assert next_run_at("monthly_end", today=created, created_on=created) == date(2026, 8, 31)
    assert next_run_at("monthly_end", today=date(2026, 12, 15), created_on=created) == (
        date(2026, 12, 31)
    )
    # Display projects the upcoming boundary; whether the previous boundary's
    # cycle ran is the batch ledger's truth, guarded by deterministic idempotency.
    assert next_run_at("quarter_end", today=date(2026, 10, 2), created_on=created) == (
        date(2026, 12, 31)
    )
    # A schedule created mid-period points at that period's own end.
    assert next_run_at("quarter_end", today=date(2026, 9, 30), created_on=created) == (
        date(2026, 9, 30)
    )


def test_create_binds_governance_identity_from_the_caller(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)

    assert schedule.tenant_id == "tenant-sg"
    assert schedule.region == "APAC"
    assert schedule.booking_center_code == "SG"
    assert schedule.owner_actor == "advisor-123"
    assert schedule.enabled is True
    audit = service.list_audit(schedule_id=schedule.schedule_id, caller_context=_caller())
    assert [record.action for record in audit] == ["created"]
    assert audit[0].changes["definition"]["schedule_id"] == schedule.schedule_id


def test_create_without_tenant_scope_fails_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ScheduleDefinitionError) as excinfo:
        service.create_schedule(
            request=_create_request(), caller_context=_caller(tenant_id=""), now=NOW
        )
    assert excinfo.value.code == "schedule_scope_unresolved"


def test_create_rejects_ungoverned_ordering_options(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ScheduleDefinitionError):
        service.create_schedule(
            request=_create_request(options={"sections": ["NOT_A_SECTION"]}),
            caller_context=_caller(),
            now=NOW,
        )


def test_an_identical_create_retry_converges_on_the_existing_schedule(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)
    retried = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)

    assert retried.schedule_id == first.schedule_id
    assert len(service.list_schedules(caller_context=_caller())) == 1


def test_a_different_definition_creates_a_second_schedule(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)
    second = service.create_schedule(
        request=_create_request(cadence="monthly_end"), caller_context=_caller(), now=NOW
    )

    assert second.schedule_id != first.schedule_id
    assert len(service.list_schedules(caller_context=_caller())) == 2


def test_schedules_are_tenant_fenced_without_an_existence_oracle(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)

    foreign = _caller(tenant_id="tenant-uk")
    assert service.list_schedules(caller_context=foreign) == []
    with pytest.raises(ScheduleDefinitionError) as real_id:
        service.get_schedule(schedule_id=schedule.schedule_id, caller_context=foreign)
    with pytest.raises(ScheduleDefinitionError) as fake_id:
        service.get_schedule(schedule_id="rbsc_does_not_exist", caller_context=foreign)
    assert real_id.value.code == fake_id.value.code == "batch_schedule_not_found"


def test_update_applies_a_diff_and_audits_it(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)

    updated = service.update_schedule(
        schedule_id=schedule.schedule_id,
        request=BatchScheduleDefinitionUpdateRequest(reporting_currency="SGD"),
        caller_context=_caller(),
        now=NOW,
    )

    assert updated.reporting_currency == "SGD"
    audit = service.list_audit(schedule_id=schedule.schedule_id, caller_context=_caller())
    assert [record.action for record in audit] == ["created", "updated"]
    assert audit[-1].changes == {"reporting_currency": {"from": "USD", "to": "SGD"}}


def test_a_no_change_update_converges_without_rewriting(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)

    unchanged = service.update_schedule(
        schedule_id=schedule.schedule_id,
        request=BatchScheduleDefinitionUpdateRequest(reporting_currency="USD"),
        caller_context=_caller(),
        now=NOW,
    )

    assert unchanged.updated_at is None
    audit = service.list_audit(schedule_id=schedule.schedule_id, caller_context=_caller())
    assert [record.action for record in audit] == ["created"]


def test_disable_and_enable_are_audited_as_their_own_actions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)

    service.update_schedule(
        schedule_id=schedule.schedule_id,
        request=BatchScheduleDefinitionUpdateRequest(enabled=False),
        caller_context=_caller(),
        now=NOW,
    )
    service.update_schedule(
        schedule_id=schedule.schedule_id,
        request=BatchScheduleDefinitionUpdateRequest(enabled=True),
        caller_context=_caller(),
        now=NOW,
    )

    audit = service.list_audit(schedule_id=schedule.schedule_id, caller_context=_caller())
    assert [record.action for record in audit] == ["created", "disabled", "enabled"]


def test_update_rejects_an_ungoverned_result_without_saving(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)

    with pytest.raises(ScheduleDefinitionError):
        service.update_schedule(
            schedule_id=schedule.schedule_id,
            request=BatchScheduleDefinitionUpdateRequest(options={"sections": ["NOT_A_SECTION"]}),
            caller_context=_caller(),
            now=NOW,
        )

    stored = service.get_schedule(schedule_id=schedule.schedule_id, caller_context=_caller())
    assert stored.options == {"sections": ["OVERVIEW", "PERFORMANCE"]}
    audit = service.list_audit(schedule_id=schedule.schedule_id, caller_context=_caller())
    assert [record.action for record in audit] == ["created"]


def test_due_definitions_bridge_into_the_scheduler_shape(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)
    disabled = service.create_schedule(
        request=_create_request(cadence="monthly_end"), caller_context=_caller(), now=NOW
    )
    service.update_schedule(
        schedule_id=disabled.schedule_id,
        request=BatchScheduleDefinitionUpdateRequest(enabled=False),
        caller_context=_caller(),
        now=NOW,
    )

    at_quarter_end = service.due_definitions_for_scheduler(
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        today=date(2026, 9, 30),
    )
    assert [definition.schedule_id for definition in at_quarter_end] == [schedule.schedule_id]
    definition = at_quarter_end[0]
    assert definition.selector_mode == "explicit_portfolio_list"
    assert definition.frequency == "quarterly"
    assert definition.as_of_date == date(2026, 9, 30)
    assert definition.portfolio_ids == ["PB_SG_GLOBAL_BAL_001"]

    before_due = service.due_definitions_for_scheduler(
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        today=date(2026, 8, 30),
    )
    assert before_due == []
    foreign_tenant = service.due_definitions_for_scheduler(
        tenant_id="tenant-uk",
        region="APAC",
        booking_center_code="SG",
        today=date(2026, 9, 30),
    )
    assert foreign_tenant == []
    foreign_region = service.due_definitions_for_scheduler(
        tenant_id="tenant-sg",
        region="EMEA",
        booking_center_code="SG",
        today=date(2026, 9, 30),
    )
    assert foreign_region == []
    foreign_booking_center = service.due_definitions_for_scheduler(
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="HK",
        today=date(2026, 9, 30),
    )
    assert foreign_booking_center == []


def test_stored_schedule_to_definition_validates_through_the_scheduler_model(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(
        request=_create_request(cadence="monthly_end"), caller_context=_caller(), now=NOW
    )

    definition = stored_schedule_to_definition(schedule, as_of_date=date(2026, 8, 31))

    assert definition.frequency == "monthly"
    assert definition.requested_output_formats == ["pdf"]
    assert definition.max_batch_size == schedule.max_batch_size


def test_create_rejects_more_portfolios_than_max_batch_size(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ScheduleDefinitionError) as excinfo:
        service.create_schedule(
            request=_create_request(portfolio_ids=["PB_1", "PB_2"], max_batch_size=1),
            caller_context=_caller(),
            now=NOW,
        )
    assert excinfo.value.code == "schedule_exceeds_max_batch_size"


def test_update_rejects_shrinking_the_bound_below_the_portfolio_count(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(
        request=_create_request(portfolio_ids=["PB_1", "PB_2"], max_batch_size=5),
        caller_context=_caller(),
        now=NOW,
    )
    with pytest.raises(ScheduleDefinitionError) as excinfo:
        service.update_schedule(
            schedule_id=schedule.schedule_id,
            request=BatchScheduleDefinitionUpdateRequest(max_batch_size=1),
            caller_context=_caller(),
            now=NOW,
        )
    assert excinfo.value.code == "schedule_exceeds_max_batch_size"


def test_an_explicit_null_clears_the_reporting_currency(tmp_path: Path) -> None:
    service = _service(tmp_path)
    schedule = service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)
    assert schedule.reporting_currency == "USD"

    cleared = service.update_schedule(
        schedule_id=schedule.schedule_id,
        request=BatchScheduleDefinitionUpdateRequest.model_validate({"reporting_currency": None}),
        caller_context=_caller(),
        now=NOW,
    )
    assert cleared.reporting_currency is None

    omitted = service.update_schedule(
        schedule_id=schedule.schedule_id,
        request=BatchScheduleDefinitionUpdateRequest(),
        caller_context=_caller(),
        now=NOW,
    )
    assert omitted.reporting_currency is None
    audit = service.list_audit(schedule_id=schedule.schedule_id, caller_context=_caller())
    assert [record.action for record in audit] == ["created", "updated"]


def test_definition_and_audit_commit_atomically(tmp_path: Path) -> None:
    """A schedule must never exist without the audit record that explains it."""

    ledger = ReportBatchLedger(tmp_path / "schedules.sqlite3")
    service = ScheduleDefinitionService(ledger)

    def _fail(connection, record):
        raise RuntimeError("audit write failed")

    original = ledger._write_schedule_audit
    ledger._write_schedule_audit = _fail
    try:
        with pytest.raises(RuntimeError):
            service.create_schedule(request=_create_request(), caller_context=_caller(), now=NOW)
    finally:
        ledger._write_schedule_audit = original

    assert ledger.list_schedule_definitions("tenant-sg") == []


def test_stored_definitions_fold_into_the_scheduler_pass_itself() -> None:
    """The daemon loop and the HTTP route share one materialization path: the
    scheduler folds due stored definitions on every pass (review finding on #197 -
    HTTP-only folding left API-created schedules dead in the deployed daemon)."""

    import asyncio

    from app.report_batch_orchestrator.scheduler import (
        BatchSchedulerConfig,
        ReportBatchScheduler,
    )

    captured: dict = {}

    class _LedgerSpy:
        def create_batch(self, *, request, caller_context, idempotency_key):
            captured["request"] = request
            captured["idempotency_key"] = idempotency_key

            class _Batch:
                batch_id = "rbch_daemon"
                status = "materialized"
                item_count = len(request.portfolio_ids)

            return _Batch()

    class _Portfolios:
        async def get_portfolio_detail(self, portfolio_id, correlation_id=None):
            return 200, {"portfolio_id": portfolio_id, "status": "active"}

    class _StoredSource:
        def due_definitions_for_scheduler(self, *, tenant_id, region, booking_center_code, today):
            captured["scope"] = (tenant_id, region, booking_center_code, today)
            return [stored_schedule_to_definition(_stored_schedule(), as_of_date=date(2026, 9, 30))]

    def _stored_schedule():
        from app.report_batch_orchestrator.schedule_definitions import (
            StoredBatchSchedule,
        )

        return StoredBatchSchedule(
            schedule_id="rbsc_daemon",
            tenant_id="tenant-sg",
            region="APAC",
            booking_center_code="SG",
            owner_actor="advisor-123",
            enabled=True,
            cadence="quarter_end",
            portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
            requested_output_formats=["pdf"],
            reporting_currency="USD",
            options={"sections": ["OVERVIEW", "PERFORMANCE"]},
            max_batch_size=10,
            created_at=NOW,
            updated_at=None,
        )

    scheduler = ReportBatchScheduler(
        batch_ledger=_LedgerSpy(),
        portfolio_source=_Portfolios(),
        stored_schedule_source=_StoredSource(),
    )
    config = BatchSchedulerConfig(
        scheduler_id="daemon-test",
        interval_seconds=60.0,
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="scheduler",
        schedules=(),
    )
    result = asyncio.run(
        scheduler.run_due_schedules(
            config=config,
            caller_context=_caller(),
            evaluation_date=date(2026, 9, 30),
        )
    )

    assert captured["scope"] == ("tenant-sg", "APAC", "SG", date(2026, 9, 30))
    assert [entry.schedule_id for entry in result.materialized] == ["rbsc_daemon"]
    assert captured["request"].options["batch_schedule_id"] == "rbsc_daemon"
