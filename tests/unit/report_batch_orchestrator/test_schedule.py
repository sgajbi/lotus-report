from __future__ import annotations

from datetime import date

import pytest

import app.report_batch_orchestrator.schedule as schedule_module
from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    BatchCycleRequest,
    PortfolioBatchCandidate,
)
from app.report_batch_orchestrator.schedule import (
    BatchScheduleValidationError,
    materialize_cycle,
    scheduled_batch_idempotency_key,
)
from app.report_batch_orchestrator.selector import BatchSelectorValidationError
from app.reporting_jobs.models import ReportCallerContext


def _caller() -> ReportCallerContext:
    return ReportCallerContext(
        triggered_by="scheduler",
        caller_application="lotus-report",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="system",
        correlation_id="corr-scheduled-batch",
        trace_id="trace-scheduled-batch",
        trigger_type="system",
    )


@pytest.mark.parametrize(
    ("frequency", "as_of_date", "period_start"),
    [
        ("monthly", date(2026, 4, 30), date(2026, 4, 1)),
        ("quarterly", date(2026, 5, 31), date(2026, 4, 1)),
        ("semi_annual", date(2026, 9, 30), date(2026, 7, 1)),
        ("yearly", date(2026, 12, 31), date(2026, 1, 1)),
    ],
)
def test_standard_frequency_cycles_are_materialized_from_as_of_date(
    frequency: str,
    as_of_date: date,
    period_start: date,
) -> None:
    cycle = materialize_cycle(
        BatchCycleRequest(
            frequency=frequency,
            as_of_date=as_of_date,
            template_id="portfolio-review",
            template_version="v1",
            render_package_version="portfolio-review.v1",
        )
    )

    assert cycle.frequency == frequency
    assert cycle.period_start == period_start
    assert cycle.period_end == as_of_date
    assert cycle.as_of_date == as_of_date
    assert cycle.idempotency_scope.startswith(f"{frequency}:{period_start.isoformat()}:")


def test_explicit_frequency_requires_valid_period_and_as_of_date() -> None:
    cycle = materialize_cycle(
        BatchCycleRequest(
            frequency="explicit",
            as_of_date="2026-04-15",
            explicit_period_start="2026-04-01",
            explicit_period_end="2026-04-30",
        )
    )

    assert cycle.period_start == date(2026, 4, 1)
    assert cycle.period_end == date(2026, 4, 30)
    assert cycle.as_of_date == date(2026, 4, 15)


@pytest.mark.parametrize(
    ("cycle_request", "expected_code"),
    [
        (
            BatchCycleRequest(frequency="explicit", as_of_date="2026-04-15"),
            "explicit_period_required",
        ),
        (
            BatchCycleRequest(
                frequency="explicit",
                as_of_date="2026-04-15",
                explicit_period_start="2026-05-01",
                explicit_period_end="2026-04-01",
            ),
            "invalid_explicit_period",
        ),
        (
            BatchCycleRequest(
                frequency="explicit",
                as_of_date="2026-05-01",
                explicit_period_start="2026-04-01",
                explicit_period_end="2026-04-30",
            ),
            "as_of_date_outside_period",
        ),
        (
            BatchCycleRequest.model_construct(
                frequency="weekly",
                as_of_date=date(2026, 4, 30),
                explicit_period_start=None,
                explicit_period_end=None,
                template_id="portfolio-review",
                template_version="v1",
                render_package_version="portfolio-review.v1",
            ),
            "unsupported_batch_frequency",
        ),
    ],
)
def test_schedule_validation_rejects_invalid_cycles(
    cycle_request: BatchCycleRequest,
    expected_code: str,
) -> None:
    with pytest.raises(BatchScheduleValidationError) as exc_info:
        materialize_cycle(cycle_request)

    assert exc_info.value.code == expected_code


def test_schedule_validation_rejects_frequency_without_period_semantics(monkeypatch) -> None:
    monkeypatch.setattr(
        schedule_module,
        "BATCH_FREQUENCIES",
        ("monthly", "quarterly", "semi_annual", "yearly", "explicit", "weekly"),
    )
    request = BatchCycleRequest.model_construct(
        frequency="weekly",
        as_of_date=date(2026, 4, 30),
        explicit_period_start=None,
        explicit_period_end=None,
        template_id="portfolio-review",
        template_version="v1",
        render_package_version="portfolio-review.v1",
    )

    with pytest.raises(BatchScheduleValidationError) as exc_info:
        materialize_cycle(request)

    assert exc_info.value.code == "unsupported_batch_frequency"


def test_scheduled_batch_idempotency_key_is_stable_and_template_sensitive() -> None:
    caller = _caller()
    cycle = materialize_cycle(BatchCycleRequest(frequency="monthly", as_of_date="2026-04-30"))
    same_cycle = materialize_cycle(BatchCycleRequest(frequency="monthly", as_of_date="2026-04-30"))
    changed_template = materialize_cycle(
        BatchCycleRequest(
            frequency="monthly",
            as_of_date="2026-04-30",
            template_version="v2",
        )
    )
    changed_package = materialize_cycle(
        BatchCycleRequest(
            frequency="monthly",
            as_of_date="2026-04-30",
            render_package_version="portfolio-review.v2",
        )
    )

    first = scheduled_batch_idempotency_key(
        caller_context=caller,
        selector_mode="explicit_portfolio_list",
        cycle=cycle,
    )
    second = scheduled_batch_idempotency_key(
        caller_context=caller,
        selector_mode="explicit_portfolio_list",
        cycle=same_cycle,
    )
    changed = scheduled_batch_idempotency_key(
        caller_context=caller,
        selector_mode="explicit_portfolio_list",
        cycle=changed_template,
    )
    changed_package_key = scheduled_batch_idempotency_key(
        caller_context=caller,
        selector_mode="explicit_portfolio_list",
        cycle=changed_package,
    )

    assert first == second
    assert first != changed
    assert first != changed_package_key


def test_scheduled_cycle_key_supports_idempotent_batch_materialization(tmp_path) -> None:
    caller = _caller()
    cycle = materialize_cycle(BatchCycleRequest(frequency="monthly", as_of_date="2026-04-30"))
    key = scheduled_batch_idempotency_key(
        caller_context=caller,
        selector_mode="explicit_portfolio_list",
        cycle=cycle,
    )
    request = BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id="PB_SG_GLOBAL_BAL_001",
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
            )
        ],
        as_of_date=cycle.as_of_date,
        options={"period_start": cycle.period_start.isoformat()},
    )
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")

    first = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key=key,
    )
    second = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key=key,
    )

    assert second == first


def test_scheduled_all_active_scope_remains_gated(tmp_path) -> None:
    caller = _caller()
    cycle = materialize_cycle(BatchCycleRequest(frequency="monthly", as_of_date="2026-04-30"))
    request = BatchCreateRequest(
        selector_mode="all_active_portfolios",
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id="PB_SG_GLOBAL_BAL_001",
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
            )
        ],
        as_of_date=cycle.as_of_date,
    )
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")

    with pytest.raises(BatchSelectorValidationError) as exc_info:
        ledger.create_batch(
            request=request,
            caller_context=caller,
            idempotency_key="scheduled-all-active",
        )

    assert exc_info.value.code == "unsupported_batch_selector"
