from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

ReportJobWorkStatus = Literal[
    "pending",
    "leased",
    "retry_pending",
    "completed",
    "failed",
]


@dataclass(frozen=True)
class ReportJobWorkItem:
    work_item_id: str
    report_job_id: str
    status: ReportJobWorkStatus
    attempt_count: int
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_acquired_at: datetime | None = None
    lease_expires_at: datetime | None = None
    last_error_category: str | None = None
    last_error_summary: str | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class ReportJobWorkRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: int = 5
    max_delay_seconds: int = 300

    def retry_delay_seconds(self, *, attempt_count: int) -> int:
        exponent = max(attempt_count - 1, 0)
        return int(min(self.base_delay_seconds * (2**exponent), self.max_delay_seconds))


@dataclass(frozen=True)
class ReportJobWorkFailureDecision:
    status: Literal["retry_pending", "failed"]
    available_at: datetime


def decide_report_job_work_failure(
    *,
    attempt_count: int,
    failed_at: datetime,
    retry_policy: ReportJobWorkRetryPolicy,
) -> ReportJobWorkFailureDecision:
    terminal = attempt_count >= retry_policy.max_attempts
    return ReportJobWorkFailureDecision(
        status="failed" if terminal else "retry_pending",
        available_at=failed_at
        + timedelta(
            seconds=retry_policy.retry_delay_seconds(
                attempt_count=attempt_count,
            ),
        ),
    )
