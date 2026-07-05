from __future__ import annotations

from app.reporting_jobs.models import ReportJobStatus

REPORT_JOB_TRANSITION_ALLOWED_FROM: dict[ReportJobStatus, frozenset[ReportJobStatus]] = {
    "collecting_data": frozenset({"accepted"}),
    "data_ready": frozenset({"accepted", "collecting_data"}),
    "rendering": frozenset({"data_ready"}),
    "completed": frozenset({"data_ready", "rendering"}),
    "archiving": frozenset({"completed"}),
    "archived": frozenset({"completed", "archiving"}),
    "failed": frozenset(
        {
            "accepted",
            "collecting_data",
            "data_ready",
            "rendering",
            "completed",
            "archiving",
        }
    ),
}

REPORT_JOB_CANCEL_BLOCKED_STATUSES: frozenset[ReportJobStatus] = frozenset(
    {
        "rendering",
        "completed",
        "archiving",
        "archived",
        "completed_with_warnings",
        "cancelled",
    }
)


def is_report_job_transition_allowed(
    *,
    current_status: str,
    to_status: ReportJobStatus,
) -> bool:
    return current_status in REPORT_JOB_TRANSITION_ALLOWED_FROM.get(to_status, frozenset())


def is_report_job_cancellable(status: str) -> bool:
    return status not in REPORT_JOB_CANCEL_BLOCKED_STATUSES
