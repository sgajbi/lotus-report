"""Durable, caller-defined recurring report-pack schedules (issue #167).

Configuration-file schedules (`batch_schedules_json`) remain deployment-owned. This
module adds the governed definition surface advisors use: a stored schedule is
created through the API under the caller's own tenant scope, audited on every
change, and materialized by the same scheduler loop - producing exactly the batch
shape a manual order produces, so lineage, archive handoff, and status surfaces
are unchanged.

Deliberate boundaries:

- Stored schedules are `explicit_portfolio_list` only. Manifest and
  all-active-portfolio selection stay deployment-owned: all-active resolution is
  precisely the tenant-attribution hazard recorded on issue #177, and a stored
  schedule must never widen scope beyond what its creator could order manually.
- Cadences are `monthly_end` and `quarter_end` - the two recurring private-banking
  pack rhythms. Cron expressions are deferred until a concrete need exists; a
  free-form cron surface would also make the governance question ("what may this
  caller schedule?") unanswerable by inspection.
- A schedule becomes due only for period ends on or after its creation date:
  creating a quarter-end pack in February must not retroactively materialize the
  December pack.
- Portfolio-scope truth is owned upstream of this module. Report cannot verify a
  portfolio's authoritative tenant today: lotus-core's discovery has no tenant
  concept (issue #177, blocked on lotus-core#798), and manual batch orders carry
  the same trust model - their candidate scope is validated by the Gateway's
  trusted-scope contract before the request reaches Report. Schedule creation
  sits behind the same front door, so portfolio-ownership admission belongs to
  the Gateway proxy step of issue #167 and to the #177 boundary work, not to a
  local check that would have nothing truthful to compare against.
- Recurrence needs no run-tracking state. The scheduler's deterministic
  cycle-identity idempotency key already makes repeated run-due calls for the
  same period converge on the same batch, so "has this cycle run?" is answered
  by the batch ledger, not by mutable schedule state.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from app.report_batch_orchestrator.scheduler import BatchScheduleDefinition
from app.report_ordering_catalogue.validation import (
    ReportOrderingSubmissionError,
    validate_report_ordering_submission,
)
from app.reporting_jobs.models import ReportCallerContext

ScheduleCadence = Literal["monthly_end", "quarter_end"]
ScheduleAuditAction = Literal["created", "updated", "enabled", "disabled"]

SCHEDULE_CADENCE_FREQUENCY: dict[ScheduleCadence, str] = {
    "monthly_end": "monthly",
    "quarter_end": "quarterly",
}


class ScheduleDefinitionError(ValueError):
    """A typed, bounded refusal for schedule-definition requests."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def month_end(day: date) -> date:
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def quarter_end(day: date) -> date:
    end_month = ((day.month - 1) // 3) * 3 + 3
    return date(day.year, end_month, calendar.monthrange(day.year, end_month)[1])


def _period_end_on_or_after(cadence: ScheduleCadence, day: date) -> date:
    if cadence == "monthly_end":
        return month_end(day)
    return quarter_end(day)


def _previous_period_end(cadence: ScheduleCadence, day: date) -> date | None:
    """The most recent period end on or before `day`."""

    candidate = _period_end_on_or_after(cadence, day)
    if candidate <= day:
        return candidate
    first_of_period = date(day.year, day.month, 1)
    if cadence == "quarter_end":
        first_of_period = date(day.year, ((day.month - 1) // 3) * 3 + 1, 1)
    if first_of_period.month == 1:
        before = date(first_of_period.year - 1, 12, 31)
    else:
        before = date(first_of_period.year, first_of_period.month - 1, 1)
        before = month_end(before)
    if cadence == "quarter_end":
        return quarter_end(before)
    return month_end(before)


def due_as_of_date(cadence: ScheduleCadence, *, today: date, created_on: date) -> date | None:
    """The as-of date run-due should materialize now, or None when nothing is due.

    Due means: the latest period end on or before today, provided that period end
    is not earlier than the schedule's creation date - a new schedule never
    back-fills periods that ended before it existed.
    """

    latest = _previous_period_end(cadence, today)
    if latest is None or latest < created_on:
        return None
    return latest


def next_run_at(cadence: ScheduleCadence, *, today: date, created_on: date) -> date:
    """The upcoming period end this schedule will materialize at.

    This is a display projection: the next boundary on or after today (never before
    the creation date). Whether the previous boundary's cycle already ran is the
    batch ledger's truth - the scheduler's deterministic idempotency means a due
    cycle materializes at most once regardless of what this projection shows.
    """

    anchor = max(today, created_on)
    return _period_end_on_or_after(cadence, anchor)


class BatchScheduleDefinitionCreateRequest(BaseModel):
    """Caller-supplied definition of a recurring report pack."""

    cadence: ScheduleCadence = Field(
        ...,
        description=(
            "Recurrence rhythm: monthly_end materializes at each month end, "
            "quarter_end at each calendar quarter end."
        ),
    )
    portfolio_ids: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Explicit portfolios in the pack. Stored schedules cannot use manifest or "
            "all-active selection; scope cannot widen beyond an explicit list the "
            "caller could order manually."
        ),
    )
    requested_output_formats: list[str] = Field(
        default_factory=lambda: ["pdf"],
        min_length=1,
        description="Output formats each scheduled batch requests.",
    )
    reporting_currency: str | None = Field(
        None,
        description="Reporting currency for the pack, or None for the portfolio default.",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Governed report-ordering options (sections and configuration).",
    )
    max_batch_size: int = Field(
        250,
        ge=1,
        description="Upper bound on portfolios materialized per scheduled batch.",
    )


class BatchScheduleDefinitionUpdateRequest(BaseModel):
    """Partial update; omitted fields keep their stored values."""

    cadence: ScheduleCadence | None = Field(None, description="New recurrence rhythm, if changing.")
    portfolio_ids: list[str] | None = Field(
        None, min_length=1, description="Replacement explicit portfolio list, if changing."
    )
    requested_output_formats: list[str] | None = Field(
        None, min_length=1, description="Replacement output formats, if changing."
    )
    reporting_currency: str | None = Field(
        None, description="Replacement reporting currency, if changing."
    )
    options: dict[str, Any] | None = Field(
        None, description="Replacement governed ordering options, if changing."
    )
    max_batch_size: int | None = Field(
        None, ge=1, description="Replacement per-batch portfolio bound, if changing."
    )
    enabled: bool | None = Field(
        None,
        description=(
            "Enable or disable the schedule. Disabling stops future runs without "
            "deleting the definition or any batch history."
        ),
    )


class StoredBatchSchedule(BaseModel):
    """A durable schedule definition with its governance identity."""

    schedule_id: str = Field(..., description="Server-minted stable schedule identity.")
    tenant_id: str = Field(..., description="Owning tenant; fenced from all others.")
    region: str = Field(..., description="Owning region, bound from the creating caller.")
    booking_center_code: str | None = Field(
        None, description="Owning booking centre, bound from the creating caller."
    )
    owner_actor: str = Field(..., description="Actor who created the schedule.")
    enabled: bool = Field(..., description="Whether run-due materializes this schedule.")
    cadence: ScheduleCadence = Field(..., description="Recurrence rhythm.")
    portfolio_ids: list[str] = Field(..., description="Explicit portfolios in the pack.")
    requested_output_formats: list[str] = Field(
        ..., description="Output formats each scheduled batch requests."
    )
    reporting_currency: str | None = Field(
        None, description="Reporting currency, or None for the portfolio default."
    )
    options: dict[str, Any] = Field(..., description="Governed report-ordering options.")
    max_batch_size: int = Field(..., description="Per-batch portfolio bound.")
    created_at: datetime = Field(..., description="Creation instant (UTC).")
    updated_at: datetime | None = Field(None, description="Last modification instant (UTC).")


class BatchScheduleAuditRecord(BaseModel):
    """One governance event in a schedule's change history."""

    audit_id: str = Field(..., description="Server-minted audit record identity.")
    schedule_id: str = Field(..., description="Schedule this event belongs to.")
    action: ScheduleAuditAction = Field(..., description="What happened.")
    actor: str = Field(..., description="Who did it.")
    correlation_id: str = Field(..., description="Request correlation id for traceability.")
    changes: dict[str, Any] = Field(
        ..., description="Changed fields as {field: {from, to}}; full definition on create."
    )
    created_at: datetime = Field(..., description="When it happened (UTC).")


class ScheduleDefinitionStore(Protocol):
    """Durable storage for schedule definitions and their audit trail."""

    def save_schedule_definition(self, schedule: StoredBatchSchedule) -> StoredBatchSchedule: ...

    def save_schedule_definition_with_audit(
        self,
        schedule: StoredBatchSchedule,
        record: BatchScheduleAuditRecord,
    ) -> StoredBatchSchedule: ...

    def get_schedule_definition(self, schedule_id: str) -> StoredBatchSchedule | None: ...

    def list_schedule_definitions(self, tenant_id: str) -> list[StoredBatchSchedule]: ...

    def append_schedule_audit(
        self, record: BatchScheduleAuditRecord
    ) -> BatchScheduleAuditRecord: ...

    def list_schedule_audit(self, schedule_id: str) -> list[BatchScheduleAuditRecord]: ...


_MUTABLE_FIELDS = (
    "cadence",
    "portfolio_ids",
    "requested_output_formats",
    "reporting_currency",
    "options",
    "max_batch_size",
    "enabled",
)


def _require_scoped_caller(caller_context: ReportCallerContext) -> None:
    if not caller_context.tenant_id or not caller_context.region:
        raise ScheduleDefinitionError(
            "schedule_scope_unresolved",
            "Schedule definition requires a caller context with tenant and region.",
        )


def _validate_portfolio_bound(schedule: StoredBatchSchedule) -> None:
    if len(schedule.portfolio_ids) > schedule.max_batch_size:
        raise ScheduleDefinitionError(
            "schedule_exceeds_max_batch_size",
            "The schedule lists more portfolios than max_batch_size allows; it would "
            "fail with batch_size_exceeded on every due date.",
        )


def _validate_ordering(request_like: StoredBatchSchedule) -> None:
    try:
        validate_report_ordering_submission(
            report_family_id="portfolio_review",
            ordering_mode_id="governed_schedule",
            requested_output_formats=request_like.requested_output_formats,
            options=request_like.options,
        )
    except ReportOrderingSubmissionError as exc:
        raise ScheduleDefinitionError(exc.code, exc.message) from exc


class ScheduleDefinitionService:
    """Create, list, and modify stored schedules under tenant governance."""

    def __init__(self, store: ScheduleDefinitionStore) -> None:
        self._store = store

    def create_schedule(
        self,
        *,
        request: BatchScheduleDefinitionCreateRequest,
        caller_context: ReportCallerContext,
        now: datetime,
    ) -> StoredBatchSchedule:
        _require_scoped_caller(caller_context)
        normalized_portfolios = list(dict.fromkeys(request.portfolio_ids))
        for existing in self._store.list_schedule_definitions(str(caller_context.tenant_id)):
            if (
                existing.enabled
                and existing.cadence == request.cadence
                and existing.portfolio_ids == normalized_portfolios
                and existing.requested_output_formats == request.requested_output_formats
                and existing.reporting_currency == request.reporting_currency
                and existing.options == request.options
                and existing.max_batch_size == request.max_batch_size
            ):
                # An identical retry converges on the schedule it already created;
                # the original create's audit record remains the single truth.
                return existing
        schedule = StoredBatchSchedule(
            schedule_id=f"rbsc_{uuid4().hex}",
            tenant_id=str(caller_context.tenant_id),
            region=str(caller_context.region),
            booking_center_code=caller_context.booking_center_code,
            owner_actor=caller_context.triggered_by,
            enabled=True,
            cadence=request.cadence,
            portfolio_ids=normalized_portfolios,
            requested_output_formats=request.requested_output_formats,
            reporting_currency=request.reporting_currency,
            options=request.options,
            max_batch_size=request.max_batch_size,
            created_at=now,
            updated_at=None,
        )
        _validate_ordering(schedule)
        _validate_portfolio_bound(schedule)
        return self._store.save_schedule_definition_with_audit(
            schedule,
            BatchScheduleAuditRecord(
                audit_id=f"rbsa_{uuid4().hex}",
                schedule_id=schedule.schedule_id,
                action="created",
                actor=caller_context.triggered_by,
                correlation_id=caller_context.correlation_id,
                changes={"definition": schedule.model_dump(mode="json")},
                created_at=now,
            ),
        )

    def get_schedule(
        self,
        *,
        schedule_id: str,
        caller_context: ReportCallerContext,
    ) -> StoredBatchSchedule:
        _require_scoped_caller(caller_context)
        schedule = self._store.get_schedule_definition(schedule_id)
        if schedule is None or schedule.tenant_id != caller_context.tenant_id:
            # Same shape for absent and foreign: a schedule id must not become an
            # existence oracle across tenants.
            raise ScheduleDefinitionError(
                "batch_schedule_not_found", "Batch schedule was not found."
            )
        return schedule

    def list_schedules(
        self,
        *,
        caller_context: ReportCallerContext,
    ) -> list[StoredBatchSchedule]:
        _require_scoped_caller(caller_context)
        return self._store.list_schedule_definitions(str(caller_context.tenant_id))

    def list_audit(
        self,
        *,
        schedule_id: str,
        caller_context: ReportCallerContext,
    ) -> list[BatchScheduleAuditRecord]:
        self.get_schedule(schedule_id=schedule_id, caller_context=caller_context)
        return self._store.list_schedule_audit(schedule_id)

    def update_schedule(
        self,
        *,
        schedule_id: str,
        request: BatchScheduleDefinitionUpdateRequest,
        caller_context: ReportCallerContext,
        now: datetime,
    ) -> StoredBatchSchedule:
        existing = self.get_schedule(schedule_id=schedule_id, caller_context=caller_context)
        updates: dict[str, Any] = {}
        changes: dict[str, Any] = {}
        for field in _MUTABLE_FIELDS:
            value = getattr(request, field)
            if field == "reporting_currency":
                # None is a meaningful value here (portfolio-default currency), so
                # only an omitted field is skipped - an explicit null clears it.
                if field not in request.model_fields_set:
                    continue
            elif value is None:
                continue
            if field == "portfolio_ids":
                value = list(dict.fromkeys(value))
            if value == getattr(existing, field):
                continue
            updates[field] = value
            changes[field] = {"from": getattr(existing, field), "to": value}
        if not updates:
            # An identical retry converges on the stored definition without a
            # rewrite: no updated_at churn, no audit noise.
            return existing
        updated = existing.model_copy(update={**updates, "updated_at": now})
        _validate_ordering(updated)
        _validate_portfolio_bound(updated)
        if set(updates) == {"enabled"}:
            action: ScheduleAuditAction = "enabled" if updates["enabled"] else "disabled"
        else:
            action = "updated"
        return self._store.save_schedule_definition_with_audit(
            updated,
            BatchScheduleAuditRecord(
                audit_id=f"rbsa_{uuid4().hex}",
                schedule_id=updated.schedule_id,
                action=action,
                actor=caller_context.triggered_by,
                correlation_id=caller_context.correlation_id,
                changes=changes,
                created_at=now,
            ),
        )

    def due_definitions_for_scheduler(
        self,
        *,
        tenant_id: str,
        region: str,
        booking_center_code: str | None,
        today: date,
    ) -> list[BatchScheduleDefinition]:
        """Enabled stored schedules due under this scheduler's full scope.

        The scheduler process runs under one configured identity; a stored schedule
        materializes only through a scheduler whose tenant AND region match its
        binding (and booking centre when both sides carry one), so an EMEA-created
        definition can never run as an APAC batch under APAC identity.
        """

        definitions: list[BatchScheduleDefinition] = []
        for schedule in self._store.list_schedule_definitions(tenant_id):
            if not schedule.enabled:
                continue
            if schedule.region != region:
                continue
            if (
                schedule.booking_center_code is not None
                and booking_center_code is not None
                and schedule.booking_center_code != booking_center_code
            ):
                continue
            as_of = due_as_of_date(
                schedule.cadence,
                today=today,
                created_on=schedule.created_at.date(),
            )
            if as_of is None:
                continue
            definitions.append(stored_schedule_to_definition(schedule, as_of_date=as_of))
        return definitions


def stored_schedule_to_definition(
    schedule: StoredBatchSchedule,
    *,
    as_of_date: date,
) -> BatchScheduleDefinition:
    """Bridge a stored schedule into the scheduler's existing definition shape.

    Reusing BatchScheduleDefinition means stored schedules flow through the same
    candidate resolution, cycle materialization, deterministic idempotency, and
    batch-option stamping as configuration schedules - one execution path, and
    `batch_schedule_id` lineage lands on every item exactly as it does today.
    """

    return BatchScheduleDefinition(
        schedule_id=schedule.schedule_id,
        enabled=schedule.enabled,
        stable_cycle_identity=True,
        selector_mode="explicit_portfolio_list",
        frequency=SCHEDULE_CADENCE_FREQUENCY[schedule.cadence],
        as_of_date=as_of_date,
        portfolio_ids=list(schedule.portfolio_ids),
        requested_output_formats=list(schedule.requested_output_formats),
        reporting_currency=schedule.reporting_currency,
        options=dict(schedule.options),
        max_batch_size=schedule.max_batch_size,
    )
