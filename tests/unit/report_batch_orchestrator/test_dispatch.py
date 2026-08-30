from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.report_batch_orchestrator.dispatch import (
    ReportBatchDispatcher,
    evaluate_back_pressure,
)
from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    BatchDispatchPolicy,
    BatchRetryPolicy,
    BatchRuntimeLoad,
    PortfolioBatchCandidate,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import ReportCallerContext, ReportJobLedgerRecord


def _caller(**overrides) -> ReportCallerContext:
    suffix = uuid4().hex
    payload = {
        "triggered_by": "advisor-123",
        "caller_application": "lotus-gateway",
        "tenant_id": "tenant-sg",
        "region": "APAC",
        "booking_center_code": "SG",
        "role": "advisor",
        "correlation_id": f"corr-batch-dispatch-{suffix}",
        "trace_id": f"trace-batch-dispatch-{suffix}",
    }
    payload.update(overrides)
    return ReportCallerContext.model_validate(payload)


def _candidate(portfolio_id: str) -> PortfolioBatchCandidate:
    return PortfolioBatchCandidate(
        portfolio_id=portfolio_id,
        tenant_id="tenant-sg",
        region="APAC",
        active=True,
        selected=True,
    )


def _request(*portfolio_ids: str) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=list(portfolio_ids),
        source_candidates=[_candidate(portfolio_id) for portfolio_id in portfolio_ids],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf", "json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


def _batch(
    ledger: ReportBatchLedger,
    *,
    idempotency_key: str,
    portfolio_count: int = 2,
):
    portfolio_ids = [
        f"PB_SG_GLOBAL_BAL_{position:03d}" for position in range(1, portfolio_count + 1)
    ]
    return ledger.create_batch(
        request=_request(*portfolio_ids),
        caller_context=_caller(),
        idempotency_key=idempotency_key,
    )


class _LeaseTokenMissingBatchLedger:
    def get_batch(self, batch_id: str) -> ReportBatchRecord:
        return ReportBatchRecord(
            batch_id=batch_id,
            selector_mode="explicit_portfolio_list",
            tenant_id="tenant-sg",
            region="APAC",
            materialized_portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
            as_of_date=date(2026, 4, 22),
            requested_output_formats=["pdf"],
            reporting_currency="USD",
            options={},
            idempotency_key="batch-missing-lease-token",
            request_hash="hash",
            status="materialized",
            item_count=1,
            created_at=datetime(2026, 4, 22, tzinfo=UTC),
            correlation_id="corr-missing-lease-token",
            trace_id="trace-missing-lease-token",
            items=[],
        )

    def count_active_batches(self) -> int:
        return 0

    def count_active_items(self) -> int:
        return 0

    def acquire_dispatch_items(
        self,
        *,
        batch_id: str,
        worker_id: str,
        lease_seconds: int,
        limit: int,
    ) -> list[ReportBatchItemRecord]:
        return [
            ReportBatchItemRecord(
                batch_item_id="rbit_missing_lease_token",
                batch_id=batch_id,
                item_position=1,
                portfolio_id="PB_SG_GLOBAL_BAL_001",
                item_idempotency_key="batch-missing-lease-token:2026-04-22:1",
                status="leased",
                source_system="lotus-core",
                source_object="PortfolioScope",
                created_at=datetime(2026, 4, 22, tzinfo=UTC),
                lease_owner=worker_id,
                lease_token=None,
            )
        ]

    def mark_item_waiting_on_report_job(
        self,
        *,
        batch_item_id: str,
        lease_token: str,
        report_job_id: str,
    ) -> ReportBatchItemRecord:
        raise AssertionError("items without lease tokens must not be marked dispatched")


class _UnusedReportJobLedger:
    def create_portfolio_review_job(self, **kwargs) -> ReportJobLedgerRecord:
        raise AssertionError("items without lease tokens must not create report jobs")


def test_back_pressure_reasons_cover_runtime_pressure_domains() -> None:
    policy = BatchDispatchPolicy(
        max_active_batches=1,
        max_active_items=2,
        max_active_upstream_jobs=3,
        max_active_render_jobs=4,
        max_active_archive_jobs=5,
    )

    assert evaluate_back_pressure(
        BatchRuntimeLoad(
            active_batches=1,
            active_items=2,
            active_upstream_jobs=3,
            active_render_jobs=4,
            active_archive_jobs=5,
        ),
        policy,
    ) == [
        "max_active_batches_reached",
        "max_active_items_reached",
        "max_active_upstream_jobs_reached",
        "max_active_render_jobs_reached",
        "max_active_archive_jobs_reached",
    ]


def test_dispatch_creates_one_report_job_per_leased_batch_item(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = _batch(batch_ledger, idempotency_key="batch-dispatch-create", portfolio_count=2)

    result = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=5),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-a",
    )

    assert result.leased_count == 2
    assert result.dispatched_count == 2
    assert result.back_pressure_reasons == []
    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert refreshed.status == "running"
    assert {item.status for item in refreshed.items} == {"waiting_on_report_job"}
    assert [item.report_job_id for item in refreshed.items] == result.report_job_ids
    jobs = [report_job_ledger.get_job(job_id) for job_id in result.report_job_ids]
    assert [
        job.portfolio_scope["portfolio_ids"][0] for job in jobs
    ] == batch.materialized_portfolio_ids
    assert all(job.as_of_date == batch.as_of_date for job in jobs)
    assert all(job.reporting_currency == batch.reporting_currency for job in jobs)
    assert all(
        job.requested_output_formats == sorted(batch.requested_output_formats) for job in jobs
    )


def test_dispatch_fails_item_durably_when_request_validation_rejects(tmp_path) -> None:
    """A batch whose options acceptance would now refuse (validation drift,
    legacy rows) must fail its items durably at materialization - never abort
    the worker pass and strand the whole lease set in an expire-and-repeat
    loop."""

    batch_ledger = ReportBatchLedger(tmp_path / "batch-invalid.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs-invalid.sqlite3")
    request = _request("PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002")
    # Simulate a legacy/drifted durably-accepted batch: the section without
    # its accepted-brief run id fails PortfolioReviewJobRequest validation.
    request = request.model_copy(
        update={"options": {"sections": ["OVERVIEW", "ADVISOR_COMMENTARY"]}}
    )
    batch = batch_ledger.create_batch(
        request=request,
        caller_context=_caller(),
        idempotency_key="batch-dispatch-invalid-options",
    )

    result = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=5),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-a",
    )

    assert result.leased_count == 2
    assert result.dispatched_count == 0
    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert {item.status for item in refreshed.items} == {"failed_terminal"}
    assert all(item.last_error_category == "validation_failed" for item in refreshed.items)


def test_dispatch_rejects_leased_item_without_lease_token() -> None:
    dispatcher = ReportBatchDispatcher(
        batch_ledger=_LeaseTokenMissingBatchLedger(),
        report_job_ledger=_UnusedReportJobLedger(),
        policy=BatchDispatchPolicy(max_active_items=5),
    )

    with pytest.raises(RuntimeError, match="batch_item_missing_lease_token"):
        dispatcher.dispatch_batch(
            batch_id="rbch_missing_lease_token",
            caller_context=_caller(),
            worker_id="worker-a",
        )


def test_dispatch_is_idempotent_after_items_have_report_jobs(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = _batch(batch_ledger, idempotency_key="batch-dispatch-idempotent", portfolio_count=1)
    dispatcher = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=5),
    )

    first = dispatcher.dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-a",
    )
    second = dispatcher.dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-b",
    )

    assert first.dispatched_count == 1
    assert second.dispatched_count == 0
    assert second.report_job_ids == []
    assert batch_ledger.get_batch(batch.batch_id).items[0].report_job_id == first.report_job_ids[0]


def test_dispatch_enforces_active_batch_limit_across_batches(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    first_batch = _batch(batch_ledger, idempotency_key="batch-dispatch-active-first")
    second_batch = _batch(batch_ledger, idempotency_key="batch-dispatch-active-second")
    dispatcher = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_batches=1, max_active_items=5),
    )

    first = dispatcher.dispatch_batch(
        batch_id=first_batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-a",
    )
    second = dispatcher.dispatch_batch(
        batch_id=second_batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-b",
    )

    assert first.dispatched_count == 2
    assert second.dispatched_count == 0
    assert second.back_pressure_reasons == ["max_active_batches_reached"]


@pytest.mark.parametrize(
    ("runtime_load", "expected_reason"),
    [
        (BatchRuntimeLoad(active_upstream_jobs=1), "max_active_upstream_jobs_reached"),
        (BatchRuntimeLoad(active_render_jobs=1), "max_active_render_jobs_reached"),
        (BatchRuntimeLoad(active_archive_jobs=1), "max_active_archive_jobs_reached"),
    ],
)
def test_dispatch_degrades_cleanly_under_external_back_pressure(
    tmp_path,
    runtime_load: BatchRuntimeLoad,
    expected_reason: str,
) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = _batch(
        batch_ledger, idempotency_key=f"batch-dispatch-{expected_reason}", portfolio_count=1
    )
    dispatcher = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(
            max_active_items=5,
            max_active_upstream_jobs=1,
            max_active_render_jobs=1,
            max_active_archive_jobs=1,
        ),
    )

    result = dispatcher.dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-a",
        runtime_load=runtime_load,
    )

    assert result.dispatched_count == 0
    assert result.back_pressure_reasons == [expected_reason]
    assert batch_ledger.get_batch(batch.batch_id).items[0].status == "materialized"


def test_lease_acquisition_blocks_active_lease_and_allows_expired_takeover(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = _batch(ledger, idempotency_key="batch-lease-expiry", portfolio_count=1)
    t0 = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)

    assert (
        ledger.acquire_dispatch_items(
            batch_id=batch.batch_id,
            worker_id="worker-a",
            lease_seconds=60,
            limit=0,
            now=t0,
        )
        == []
    )

    first = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-a",
        lease_seconds=60,
        limit=1,
        now=t0,
    )
    assert len(first) == 1
    assert first[0].lease_owner == "worker-a"
    assert first[0].lease_token is not None

    blocked = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-b",
        lease_seconds=60,
        limit=1,
        now=t0 + timedelta(seconds=30),
    )
    assert blocked == []

    heartbeat = ledger.heartbeat_item_lease(
        batch_item_id=first[0].batch_item_id,
        lease_token=first[0].lease_token,
        lease_seconds=120,
        now=t0 + timedelta(seconds=30),
    )
    assert heartbeat.lease_expires_at == t0 + timedelta(seconds=150)

    still_blocked = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-b",
        lease_seconds=60,
        limit=1,
        now=t0 + timedelta(seconds=120),
    )
    assert still_blocked == []

    takeover = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-b",
        lease_seconds=60,
        limit=1,
        now=t0 + timedelta(seconds=151),
    )
    assert len(takeover) == 1
    assert takeover[0].lease_owner == "worker-b"
    assert takeover[0].lease_token != first[0].lease_token


def test_stale_lease_token_cannot_mark_item_dispatched(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = _batch(ledger, idempotency_key="batch-stale-lease-token", portfolio_count=1)
    [item] = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-a",
        lease_seconds=60,
        limit=1,
    )

    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        ledger.mark_item_waiting_on_report_job(
            batch_item_id=item.batch_item_id,
            lease_token="stale-token",
            report_job_id="rjob_123",
        )


def test_stale_lease_token_cannot_heartbeat_item(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = _batch(ledger, idempotency_key="batch-stale-heartbeat-token", portfolio_count=1)
    [item] = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-a",
        lease_seconds=60,
        limit=1,
    )

    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        ledger.heartbeat_item_lease(
            batch_item_id=item.batch_item_id,
            lease_token="stale-token",
            lease_seconds=60,
        )


def test_sqlite_schema_upgrade_adds_dispatch_columns_to_existing_batch_item_table(
    tmp_path,
) -> None:
    db_path = tmp_path / "legacy-batch.sqlite3"
    with closing(sqlite3.connect(db_path)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE report_batch_item (
                    batch_item_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    item_position INTEGER NOT NULL,
                    portfolio_id TEXT NOT NULL,
                    item_idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    source_object TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    ReportBatchLedger(db_path)

    with closing(sqlite3.connect(db_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(report_batch_item)")}
    assert {
        "report_job_id",
        "lease_owner",
        "lease_token",
        "lease_acquired_at",
        "lease_expires_at",
        "last_heartbeat_at",
        "dispatched_at",
    }.issubset(columns)


def test_concurrent_workers_do_not_duplicate_item_dispatch(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = _batch(batch_ledger, idempotency_key="batch-concurrent-dispatch", portfolio_count=4)

    def _dispatch(worker_id: str):
        return ReportBatchDispatcher(
            batch_ledger=batch_ledger,
            report_job_ledger=report_job_ledger,
            policy=BatchDispatchPolicy(max_active_items=4),
        ).dispatch_batch(
            batch_id=batch.batch_id,
            caller_context=_caller(),
            worker_id=worker_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(_dispatch, ["worker-a", "worker-b"]))

    refreshed = batch_ledger.get_batch(batch.batch_id)
    report_job_ids = [item.report_job_id for item in refreshed.items]
    assert sum(result.dispatched_count for result in results) == 4
    assert all(report_job_id is not None for report_job_id in report_job_ids)
    assert len(set(report_job_ids)) == 4


def test_pause_blocks_dispatch_until_batch_is_resumed(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = _batch(batch_ledger, idempotency_key="batch-pause-resume", portfolio_count=1)

    paused = batch_ledger.pause_batch(batch_id=batch.batch_id)
    blocked = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=5),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-paused",
    )
    paused_item_status = batch_ledger.get_batch(batch.batch_id).items[0].status
    resumed = batch_ledger.resume_batch(batch_id=batch.batch_id)
    dispatched = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=5),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-resumed",
    )

    assert paused.batch_status == "paused"
    assert blocked.dispatched_count == 0
    assert paused_item_status == "materialized"
    assert resumed.batch_status == "materialized"
    assert dispatched.dispatched_count == 1


def test_retry_failed_items_resets_only_retryable_due_items(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = _batch(ledger, idempotency_key="batch-retry-failed-only", portfolio_count=3)
    due_at = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)
    later = due_at + timedelta(minutes=10)

    due_retryable = ledger.mark_item_failed(
        batch_item_id=batch.items[0].batch_item_id,
        error_category="upstream_data_collection_failure",
        error_summary="lotus-performance returned a transient failure",
        retryable=True,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        next_retry_at=due_at,
        now=due_at - timedelta(minutes=5),
    )
    future_retryable = ledger.mark_item_failed(
        batch_item_id=batch.items[1].batch_item_id,
        error_category="archive_handoff_failure",
        error_summary="archive service retry window has not opened yet",
        retryable=True,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        next_retry_at=later,
        now=due_at - timedelta(minutes=5),
    )
    terminal = ledger.mark_item_failed(
        batch_item_id=batch.items[2].batch_item_id,
        error_category="selector_validation_failure",
        error_summary="portfolio is no longer eligible",
        retryable=True,
        retry_policy=BatchRetryPolicy(max_attempts=1),
        next_retry_at=due_at,
        now=due_at - timedelta(minutes=5),
    )

    result = ledger.retry_failed_items(
        batch_id=batch.batch_id,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        now=due_at,
    )
    refreshed = ledger.get_batch(batch.batch_id)

    assert due_retryable.status == "failed_retryable"
    assert future_retryable.status == "failed_retryable"
    assert terminal.status == "failed_terminal"
    assert result.affected_count == 1
    assert refreshed.items[0].status == "materialized"
    assert refreshed.items[0].attempt_count == 1
    assert refreshed.items[0].retry_eligible is False
    assert refreshed.items[1].status == "failed_retryable"
    assert refreshed.items[2].status == "failed_terminal"


def test_retry_failed_items_does_not_requeue_items_with_report_jobs(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = _batch(ledger, idempotency_key="batch-retry-job-boundary", portfolio_count=1)
    retry_at = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)
    [leased] = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-a",
        lease_seconds=60,
        limit=1,
    )
    ledger.mark_item_waiting_on_report_job(
        batch_item_id=leased.batch_item_id,
        lease_token=leased.lease_token or "",
        report_job_id="rjob_existing",
    )
    failed = ledger.mark_item_failed(
        batch_item_id=leased.batch_item_id,
        error_category="render_failure",
        error_summary="render worker failed after report job creation",
        retryable=True,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        next_retry_at=retry_at,
        now=retry_at - timedelta(minutes=5),
    )

    result = ledger.retry_failed_items(
        batch_id=batch.batch_id,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        now=retry_at,
    )
    refreshed = ledger.get_batch(batch.batch_id)
    dispatch_after_retry = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-b",
        lease_seconds=60,
        limit=1,
    )

    assert failed.status == "failed_retryable"
    assert result.affected_count == 0
    assert refreshed.items[0].status == "failed_retryable"
    assert refreshed.items[0].report_job_id == "rjob_existing"
    assert dispatch_after_retry == []


def test_cancel_batch_cancels_only_items_without_created_report_jobs(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = _batch(ledger, idempotency_key="batch-cancel-boundary", portfolio_count=2)
    [first] = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-a",
        lease_seconds=60,
        limit=1,
    )
    ledger.mark_item_waiting_on_report_job(
        batch_item_id=first.batch_item_id,
        lease_token=first.lease_token or "",
        report_job_id="rjob_existing",
    )

    result = ledger.cancel_batch(batch_id=batch.batch_id)
    refreshed = ledger.get_batch(batch.batch_id)
    dispatch_after_cancel = ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-b",
        lease_seconds=60,
        limit=2,
    )

    assert result.batch_status == "cancelled"
    assert result.affected_count == 1
    assert [item.status for item in refreshed.items] == [
        "waiting_on_report_job",
        "cancelled",
    ]
    assert refreshed.items[0].report_job_id == "rjob_existing"
    assert dispatch_after_cancel == []


def test_recovery_scanner_is_idempotent_and_allows_safe_redispatch(tmp_path) -> None:
    batch_ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    report_job_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch = _batch(batch_ledger, idempotency_key="batch-expired-lease-recovery", portfolio_count=1)
    t0 = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)
    [leased] = batch_ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-a",
        lease_seconds=60,
        limit=1,
        now=t0,
    )

    recovered = batch_ledger.recover_expired_leases(
        batch_id=batch.batch_id,
        now=t0 + timedelta(seconds=61),
    )
    repeated = batch_ledger.recover_expired_leases(
        batch_id=batch.batch_id,
        now=t0 + timedelta(seconds=62),
    )
    result = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_items=5),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=_caller(),
        worker_id="worker-b",
    )
    refreshed = batch_ledger.get_batch(batch.batch_id)

    assert recovered.recovered_count == 1
    assert recovered.recovery_pending_item_ids == [leased.batch_item_id]
    assert repeated.recovered_count == 0
    assert result.dispatched_count == 1
    assert refreshed.items[0].status == "waiting_on_report_job"
    assert refreshed.items[0].last_error_category == "expired_item_lease"


def test_failed_single_item_batch_reconciles_terminal_and_retryable_status(tmp_path) -> None:
    terminal_ledger = ReportBatchLedger(tmp_path / "terminal.sqlite3")
    terminal_batch = _batch(
        terminal_ledger,
        idempotency_key="batch-single-terminal",
        portfolio_count=1,
    )
    retryable_ledger = ReportBatchLedger(tmp_path / "retryable.sqlite3")
    retryable_batch = _batch(
        retryable_ledger,
        idempotency_key="batch-single-retryable",
        portfolio_count=1,
    )
    failed_at = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)

    terminal_item = terminal_ledger.mark_item_failed(
        batch_item_id=terminal_batch.items[0].batch_item_id,
        error_category="selector_validation_failure",
        error_summary="portfolio is no longer eligible",
        retryable=False,
        now=failed_at,
    )
    retryable_item = retryable_ledger.mark_item_failed(
        batch_item_id=retryable_batch.items[0].batch_item_id,
        error_category="upstream_data_collection_failure",
        error_summary="source system unavailable",
        retryable=True,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        next_retry_at=failed_at + timedelta(minutes=5),
        now=failed_at,
    )

    assert terminal_item.status == "failed_terminal"
    assert terminal_ledger.get_batch(terminal_batch.batch_id).status == "completed_with_failures"
    assert retryable_item.status == "failed_retryable"
    assert retryable_ledger.get_batch(retryable_batch.batch_id).status == "failed"


def test_mark_item_failed_rejects_unknown_item(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")

    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        ledger.mark_item_failed(
            batch_item_id="missing",
            error_category="upstream_data_collection_failure",
            error_summary="missing item",
            retryable=True,
        )
