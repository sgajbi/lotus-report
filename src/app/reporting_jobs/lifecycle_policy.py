from __future__ import annotations

from app.reporting_jobs.models import ReportJobStatus

REPORT_JOB_TRANSITION_ALLOWED_FROM: dict[ReportJobStatus, frozenset[ReportJobStatus]] = {
    "collecting_data": frozenset({"accepted"}),
    "data_ready": frozenset({"accepted", "collecting_data"}),
    "rendering": frozenset({"data_ready"}),
    "completed": frozenset({"data_ready", "rendering"}),
    "archiving": frozenset({"completed"}),
    # "failed" is admitted for exactly one flow: replay's archive-ambiguity
    # resolution, where the archive lookup proves the original
    # arch_{render_job_id} request committed and the failure classification
    # was a transport artifact - the truthful terminal state is archived.
    "archived": frozenset({"completed", "archiving", "failed"}),
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
