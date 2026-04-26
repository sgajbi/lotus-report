from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
    import sqlite3

    db_path = tmp_path / "legacy-batch.sqlite3"
    with sqlite3.connect(db_path) as connection:
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

    with sqlite3.connect(db_path) as connection:
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
