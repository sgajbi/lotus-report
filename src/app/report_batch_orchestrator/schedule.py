from __future__ import annotations

import hashlib
from datetime import date

from app.report_batch_orchestrator.contracts import BATCH_FREQUENCIES, BatchSelectorMode
from app.report_batch_orchestrator.models import BatchCycle, BatchCycleRequest
from app.reporting_jobs.ledger import canonical_json
from app.reporting_jobs.models import ReportCallerContext


class BatchScheduleValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


def materialize_cycle(request: BatchCycleRequest) -> BatchCycle:
    if request.frequency not in BATCH_FREQUENCIES:
        raise BatchScheduleValidationError(
            "unsupported_batch_frequency",
            f"Batch frequency {request.frequency!r} is not supported.",
        )

    if request.frequency == "explicit":
        period_start, period_end = _explicit_period(request)
    elif request.frequency == "monthly":
        period_start = date(request.as_of_date.year, request.as_of_date.month, 1)
        period_end = request.as_of_date
    elif request.frequency == "quarterly":
        start_month = ((request.as_of_date.month - 1) // 3) * 3 + 1
        period_start = date(request.as_of_date.year, start_month, 1)
        period_end = request.as_of_date
    elif request.frequency == "semi_annual":
        start_month = 1 if request.as_of_date.month <= 6 else 7
        period_start = date(request.as_of_date.year, start_month, 1)
        period_end = request.as_of_date
    elif request.frequency == "yearly":
        period_start = date(request.as_of_date.year, 1, 1)
        period_end = request.as_of_date
    else:
        raise BatchScheduleValidationError(
            "unsupported_batch_frequency",
            f"Batch frequency {request.frequency!r} is not supported.",
        )

    return BatchCycle(
        frequency=request.frequency,
        period_start=period_start,
        period_end=period_end,
        as_of_date=request.as_of_date,
        idempotency_scope=_cycle_scope(
            frequency=request.frequency,
            period_start=period_start,
            period_end=period_end,
            as_of_date=request.as_of_date,
        ),
    )


def scheduled_batch_idempotency_key(
    *,
    caller_context: ReportCallerContext,
    selector_mode: BatchSelectorMode,
    cycle: BatchCycle,
    selector_identity: str | None = None,
) -> str:
    payload = {
        "tenant_id": caller_context.tenant_id,
        "region": caller_context.region,
        "selector_mode": selector_mode,
        "cycle": cycle.idempotency_scope,
    }
    if selector_identity is not None:
        payload["selector_identity"] = selector_identity
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:32]
    return f"scheduled-batch-{digest}"


def _explicit_period(request: BatchCycleRequest) -> tuple[date, date]:
    if request.explicit_period_start is None or request.explicit_period_end is None:
        raise BatchScheduleValidationError(
            "explicit_period_required",
            "Explicit batch frequency requires explicit period start and end dates.",
        )
    if request.explicit_period_start > request.explicit_period_end:
        raise BatchScheduleValidationError(
            "invalid_explicit_period",
            "Explicit period start must be on or before explicit period end.",
        )
    if not request.explicit_period_start <= request.as_of_date <= request.explicit_period_end:
        raise BatchScheduleValidationError(
            "as_of_date_outside_period",
            "Explicit as-of date must fall within the explicit period.",
        )
    return request.explicit_period_start, request.explicit_period_end


def _cycle_scope(
    *,
    frequency: str,
    period_start: date,
    period_end: date,
    as_of_date: date,
) -> str:
    """Cycle identity is the BUSINESS cycle - frequency, period, as-of.

    Template and package versions are presentation contracts resolved at job
    acceptance; hashing them here meant a deployment that moved a default
    could mint a second batch for the same business cycle (report#283
    finding E). They no longer participate.
    """

    payload = {
        "frequency": frequency,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "as_of_date": as_of_date.isoformat(),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]
    return f"{frequency}:{period_start.isoformat()}:{period_end.isoformat()}:{digest}"
