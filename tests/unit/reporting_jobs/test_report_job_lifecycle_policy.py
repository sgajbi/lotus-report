import pytest

from app.reporting_jobs.lifecycle_policy import (
    REPORT_JOB_CANCEL_BLOCKED_STATUSES,
    REPORT_JOB_TRANSITION_ALLOWED_FROM,
    is_report_job_cancellable,
    is_report_job_transition_allowed,
)


@pytest.mark.parametrize(
    ("to_status", "allowed_from"),
    sorted(REPORT_JOB_TRANSITION_ALLOWED_FROM.items()),
)
def test_report_job_transition_policy_allows_declared_sources(to_status, allowed_from):
    for current_status in allowed_from:
        assert is_report_job_transition_allowed(
            current_status=current_status,
            to_status=to_status,
        )


@pytest.mark.parametrize(
    ("current_status", "to_status"),
    [
        ("accepted", "rendering"),
        ("rendering", "data_ready"),
        ("archived", "failed"),
        ("cancelled", "collecting_data"),
    ],
)
def test_report_job_transition_policy_rejects_invalid_sources(current_status, to_status):
    assert not is_report_job_transition_allowed(
        current_status=current_status,
        to_status=to_status,
    )


def test_report_job_cancel_policy_blocks_render_and_terminal_states():
    for status in REPORT_JOB_CANCEL_BLOCKED_STATUSES:
        assert not is_report_job_cancellable(status)

    assert is_report_job_cancellable("accepted")
    assert is_report_job_cancellable("collecting_data")
    assert is_report_job_cancellable("data_ready")
    assert is_report_job_cancellable("failed")
