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


def test_cycle_identity_is_the_business_cycle_not_the_template() -> None:
    """report#283 finding E, inverted into the invariant: template and
    package versions are presentation contracts resolved at job acceptance -
    a deployment that moves a default must NOT mint a second batch for the
    same business cycle. The legacy scope keeps the old sensitivity solely
    so pre-change batches are recognised and skipped."""

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
    other_cycle = materialize_cycle(BatchCycleRequest(frequency="monthly", as_of_date="2026-05-31"))

    def key(target):
        return scheduled_batch_idempotency_key(
            caller_context=caller,
            selector_mode="explicit_portfolio_list",
            cycle=target,
        )

    assert key(cycle) == key(same_cycle)
    # One business cycle, one batch - whatever the current template default.
    assert key(cycle) == key(changed_template)
    assert key(cycle) != key(other_cycle)
    # The legacy scope retains template sensitivity for pre-change lookup.
    assert cycle.legacy_idempotency_scope != changed_template.legacy_idempotency_scope
    assert cycle.idempotency_scope == changed_template.idempotency_scope


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


def test_scheduled_all_active_scope_materializes_source_backed_candidates(tmp_path) -> None:
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

    batch = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key="scheduled-all-active",
    )

    assert batch.selector_mode == "all_active_portfolios"
    assert batch.materialized_portfolio_ids == ["PB_SG_GLOBAL_BAL_001"]


def test_scheduled_manifest_scope_materializes_explicit_manifest_entries(tmp_path) -> None:
    caller = _caller()
    cycle = materialize_cycle(BatchCycleRequest(frequency="monthly", as_of_date="2026-04-30"))
    request = BatchCreateRequest(
        selector_mode="batch_manifest",
        portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id="PB_SG_GLOBAL_BAL_001",
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
                source_system="lotus-operations",
                source_object="BatchManifest",
            )
        ],
        as_of_date=cycle.as_of_date,
    )
    ledger = ReportBatchLedger(tmp_path / "scheduled.sqlite3")

    batch = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key="scheduled-manifest",
    )

    assert batch.selector_mode == "batch_manifest"
    assert batch.materialized_portfolio_ids == ["PB_SG_GLOBAL_BAL_001"]
    assert batch.items[0].source_system == "lotus-operations"


def test_a_cycle_materialized_under_the_legacy_identity_is_not_rerun(tmp_path) -> None:
    """The transition guarantee: a batch created under the old
    template-bearing key is recognised via the legacy scope and skipped -
    the identity-formula change itself must not manufacture a second run
    of the same business cycle."""

    caller = _caller()
    cycle = materialize_cycle(
        BatchCycleRequest(frequency="monthly", as_of_date="2026-04-30", template_version="v1")
    )
    legacy_key = scheduled_batch_idempotency_key(
        caller_context=caller,
        selector_mode="explicit_portfolio_list",
        cycle=cycle.model_copy(update={"idempotency_scope": cycle.legacy_idempotency_scope}),
    )
    new_key = scheduled_batch_idempotency_key(
        caller_context=caller,
        selector_mode="explicit_portfolio_list",
        cycle=cycle,
    )
    assert legacy_key != new_key

    ledger = ReportBatchLedger(tmp_path / "batches.sqlite3")
    ledger.create_batch(
        request=BatchCreateRequest(
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
            options={},
        ),
        caller_context=caller,
        idempotency_key=legacy_key,
    )

    assert ledger.has_batch_for_idempotency_key(legacy_key) is True
    assert ledger.has_batch_for_idempotency_key(new_key) is False
