from datetime import UTC, datetime, timedelta

import pytest

from app.reporting_jobs.ledger import (
    InvalidReportJobWorkTransitionError,
    ReportJobLedger,
)
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
from app.reporting_render.replay_service import assert_replay_eligible


def _submit(ledger: ReportJobLedger):
    return ledger.submit_portfolio_review_job(
        request=PortfolioReviewJobRequest.model_validate(
            {
                "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
                "as_of_date": "2026-04-10",
                "requested_output_formats": ["pdf"],
                "reporting_currency": "USD",
                "options": {"sections": ["OVERVIEW"]},
            }
        ),
        caller_context=ReportCallerContext(
            triggered_by="advisor-1",
            caller_application="lotus-gateway",
            tenant_id="tenant-sg",
            region="APAC",
            correlation_id="corr-1",
            trace_id="trace-1",
        ),
        idempotency_key="report-order-1",
    )


def test_submission_atomically_creates_one_idempotent_work_item(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.sqlite3")
    first = _submit(ledger)
    second = _submit(ledger)

    assert second.job_id == first.job_id
    work_item = ledger.get_work_item_for_job(first.job_id)
    assert work_item is not None
    assert work_item.status == "pending"
    assert work_item.attempt_count == 0


def test_claim_complete_requires_owned_lease(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.sqlite3")
    job = _submit(ledger)
    now = datetime.now(UTC) + timedelta(seconds=1)

    claimed = ledger.claim_work_items(worker_id="worker-1", limit=1, lease_seconds=60, now=now)
    assert [item.report_job_id for item in claimed] == [job.job_id]
    assert claimed[0].attempt_count == 1
    assert claimed[0].lease_token is not None

    with pytest.raises(InvalidReportJobWorkTransitionError):
        ledger.complete_work_item(
            work_item_id=claimed[0].work_item_id,
            lease_token="wrong-token",
            now=now,
        )

    completed = ledger.complete_work_item(
        work_item_id=claimed[0].work_item_id,
        lease_token=claimed[0].lease_token or "",
        now=now,
    )
    assert completed.status == "completed"
    assert completed.completed_at == now
    assert ledger.claim_work_items(worker_id="worker-2", limit=1, lease_seconds=60, now=now) == []


def test_failure_retries_with_backoff_then_becomes_terminal(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.sqlite3")
    _submit(ledger)
    now = datetime.now(UTC) + timedelta(seconds=1)
    policy = ReportJobWorkRetryPolicy(max_attempts=2, base_delay_seconds=10)

    first = ledger.claim_work_items(worker_id="worker-1", limit=1, lease_seconds=60, now=now)[0]
    retry = ledger.fail_work_item(
        work_item_id=first.work_item_id,
        lease_token=first.lease_token or "",
        error_category="worker_failure",
        error_summary="temporary failure\nwith detail",
        retry_policy=policy,
        now=now,
    )
    assert retry.status == "retry_pending"
    assert retry.available_at == now + timedelta(seconds=10)
    assert retry.last_error_summary == "temporary failure with detail"
    assert ledger.claim_work_items(worker_id="worker-2", limit=1, lease_seconds=60, now=now) == []

    second = ledger.claim_work_items(
        worker_id="worker-2",
        limit=1,
        lease_seconds=60,
        now=now + timedelta(seconds=10),
    )[0]
    failed = ledger.fail_work_item(
        work_item_id=second.work_item_id,
        lease_token=second.lease_token or "",
        error_category="worker_failure",
        error_summary="still failing",
        retry_policy=policy,
        now=now + timedelta(seconds=10),
    )
    assert failed.status == "failed"
    assert failed.attempt_count == 2
    source_job = ledger.get_job(failed.report_job_id)
    assert source_job.status == "failed"
    assert source_job.failure_category == "operator_intervention_required"
    assert source_job.retry_eligible is True
    assert source_job.current_step == "failed"
    assert_replay_eligible(source_job)
    assert [event.to_status for event in ledger.list_status_events(source_job.job_id)] == [
        "accepted",
        "failed",
    ]


def test_expired_lease_is_recovered_and_reclaimed(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.sqlite3")
    _submit(ledger)
    now = datetime.now(UTC) + timedelta(seconds=1)
    first = ledger.claim_work_items(worker_id="worker-1", limit=1, lease_seconds=30, now=now)[0]

    reclaimed = ledger.claim_work_items(
        worker_id="worker-2",
        limit=1,
        lease_seconds=30,
        retry_policy=ReportJobWorkRetryPolicy(base_delay_seconds=10),
        now=now + timedelta(seconds=41),
    )[0]
    assert reclaimed.work_item_id == first.work_item_id
    assert reclaimed.lease_owner == "worker-2"
    assert reclaimed.attempt_count == 2
    assert reclaimed.last_error_category == "expired_work_lease"


def test_expired_lease_exhaustion_terminalizes_work_and_source_job(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.sqlite3")
    job = _submit(ledger)
    now = datetime.now(UTC) + timedelta(seconds=1)
    policy = ReportJobWorkRetryPolicy(max_attempts=1, base_delay_seconds=10)
    leased = ledger.claim_work_items(
        worker_id="worker-1",
        limit=1,
        lease_seconds=30,
        retry_policy=policy,
        now=now,
    )[0]

    assert (
        ledger.claim_work_items(
            worker_id="worker-2",
            limit=1,
            lease_seconds=30,
            retry_policy=policy,
            now=now + timedelta(seconds=31),
        )
        == []
    )

    exhausted = ledger.get_work_item_for_job(job.job_id)
    assert exhausted is not None
    assert exhausted.work_item_id == leased.work_item_id
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 1
    assert exhausted.last_error_category == "expired_work_lease"
    source_job = ledger.get_job(job.job_id)
    assert source_job.status == "failed"
    assert source_job.failure_category == "timeout"
    assert source_job.retry_eligible is True
    events = ledger.list_status_events(job.job_id)
    assert [event.to_status for event in events] == ["accepted", "failed"]
    assert events[-1].correlation_id == "corr-1"
    assert events[-1].trace_id == "trace-1"


def test_repeated_expiry_never_claims_beyond_attempt_policy(tmp_path):
    ledger = ReportJobLedger(tmp_path / "report.sqlite3")
    job = _submit(ledger)
    now = datetime.now(UTC) + timedelta(seconds=1)
    policy = ReportJobWorkRetryPolicy(max_attempts=2, base_delay_seconds=10)
    first = ledger.claim_work_items(
        worker_id="worker-1",
        limit=1,
        lease_seconds=30,
        retry_policy=policy,
        now=now,
    )[0]

    second = ledger.claim_work_items(
        worker_id="worker-2",
        limit=1,
        lease_seconds=30,
        retry_policy=policy,
        now=now + timedelta(seconds=41),
    )[0]
    assert second.work_item_id == first.work_item_id
    assert second.attempt_count == 2

    assert (
        ledger.claim_work_items(
            worker_id="worker-3",
            limit=1,
            lease_seconds=30,
            retry_policy=policy,
            now=now + timedelta(seconds=72),
        )
        == []
    )
    exhausted = ledger.get_work_item_for_job(job.job_id)
    assert exhausted is not None
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 2
    assert ledger.get_job(job.job_id).status == "failed"
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "failed",
    ]
