import pytest

from app.report_batch_orchestrator.lifecycle_policy import (
    batch_item_failure_outcome,
    reconciled_batch_status,
)


@pytest.mark.parametrize(
    ("retryable", "attempt_count", "max_attempts", "expected_status", "expected_retry_eligible"),
    [
        (True, 1, 3, "failed_retryable", True),
        (True, 2, 3, "failed_retryable", True),
        (True, 3, 3, "failed_terminal", False),
        (False, 1, 3, "failed_terminal", False),
    ],
)
def test_batch_item_failure_outcome(
    retryable,
    attempt_count,
    max_attempts,
    expected_status,
    expected_retry_eligible,
):
    assert batch_item_failure_outcome(
        retryable=retryable,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    ) == (expected_status, expected_retry_eligible)


@pytest.mark.parametrize(
    ("item_statuses", "expected_batch_status"),
    [
        (["succeeded"], "completed"),
        (["succeeded", "succeeded"], "completed"),
        (["cancelled"], "cancelled"),
        (["succeeded", "cancelled"], "cancelled"),
        (["succeeded", "failed_terminal"], "completed_with_failures"),
        (["failed_retryable"], "failed"),
        (["succeeded", "failed_retryable"], "failed"),
        (["succeeded", "leased"], None),
        (["materialized"], None),
        ([], None),
    ],
)
def test_reconciled_batch_status(item_statuses, expected_batch_status):
    assert reconciled_batch_status(item_statuses) == expected_batch_status
