from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher
from app.report_batch_orchestrator.ledger import (
    BatchIdempotencyConflictError,
    MissingBatchIdempotencyKeyError,
)
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    BatchDispatchPolicy,
    BatchRetryPolicy,
    BatchRuntimeLoad,
    PortfolioBatchCandidate,
)
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger
from app.reporting_jobs.models import ReportCallerContext
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger


class _FakeRows:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        if not self._rows:
            return None
        return self._rows[0]


class _MissingSchemaConnection:
    def execute(self, query: str, params: object = None) -> _FakeRows:
        return _FakeRows([{"table_name": "report_batch"}])

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class _UniqueViolationInsertConnection:
    def execute(self, query: str, params: object = None) -> _FakeRows:
        if "SELECT batch_id, request_hash" in query:
            return _FakeRows([])
        if "INSERT INTO report_batch" in query:
            raise UniqueViolation("duplicate key")
        return _FakeRows([])

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def _postgres_ledger_with_connections(
    *connections: object,
) -> PostgresReportBatchLedger:
    ledger = PostgresReportBatchLedger.__new__(PostgresReportBatchLedger)
    connection_iter = iter(connections)

    @contextmanager
    def _connect():
        yield next(connection_iter)

    ledger._connect = _connect  # type: ignore[method-assign]
    return ledger


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL batch ledger proof")
    return database_url


def _caller(unique_suffix: str) -> ReportCallerContext:
    return ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id=f"corr-pg-batch-{unique_suffix}",
        trace_id=f"trace-pg-batch-{unique_suffix}",
    )


def _request(unique_suffix: str, portfolio_id: str) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=[portfolio_id],
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id=portfolio_id,
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
            )
        ],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW"], "proof": unique_suffix},
    )


def _multi_request(unique_suffix: str, portfolio_ids: list[str]) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=portfolio_ids,
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id=portfolio_id,
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
            )
            for portfolio_id in portfolio_ids
        ],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf", "json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"], "proof": unique_suffix},
    )


def test_postgres_check_ready_reports_missing_schema_tables() -> None:
    ledger = _postgres_ledger_with_connections(_MissingSchemaConnection())

    with pytest.raises(RuntimeError, match="report_batch_ledger_schema_missing:report_batch_item"):
        ledger.check_ready()


def test_postgres_unique_violation_without_existing_batch_is_conflict() -> None:
    unique_suffix = uuid4().hex
    ledger = _postgres_ledger_with_connections(
        _UniqueViolationInsertConnection(),
        _UniqueViolationInsertConnection(),
    )

    with pytest.raises(BatchIdempotencyConflictError, match="batch_idempotency_unique_violation"):
        ledger.create_batch(
            request=_request(unique_suffix, f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"),
            caller_context=_caller(unique_suffix),
            idempotency_key=f"batch-pg-unique-race-{unique_suffix}",
        )


def test_postgres_batch_ledger_persists_idempotent_materialized_batch() -> None:
    unique_suffix = uuid4().hex
    ledger = PostgresReportBatchLedger(_database_url())
    ledger.check_ready()
    request = _request(unique_suffix, f"PB_SG_GLOBAL_BAL_001_{unique_suffix}")
    caller = _caller(unique_suffix)

    first = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key=f"batch-pg-{unique_suffix}",
    )
    second = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key=f"batch-pg-{unique_suffix}",
    )

    assert second == first
    assert first.item_count == 1
    assert first.items[0].portfolio_id == f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"

    with pytest.raises(BatchIdempotencyConflictError):
        ledger.create_batch(
            request=_request(unique_suffix, f"PB_SG_GLOBAL_BAL_002_{unique_suffix}"),
            caller_context=caller,
            idempotency_key=f"batch-pg-{unique_suffix}",
        )


def test_postgres_batch_rejects_missing_idempotency_key() -> None:
    unique_suffix = uuid4().hex
    ledger = PostgresReportBatchLedger(_database_url())

    with pytest.raises(MissingBatchIdempotencyKeyError, match="missing_batch_idempotency_key"):
        ledger.create_batch(
            request=_request(unique_suffix, f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"),
            caller_context=_caller(unique_suffix),
            idempotency_key=" ",
        )


def test_postgres_batch_get_missing_batch_raises_not_found() -> None:
    ledger = PostgresReportBatchLedger(_database_url())

    with pytest.raises(ValueError, match="report_batch_not_found"):
        ledger.get_batch(f"rbch_missing_{uuid4().hex}")


def test_postgres_batch_dispatch_persists_report_jobs_and_item_state() -> None:
    unique_suffix = uuid4().hex
    batch_ledger = PostgresReportBatchLedger(_database_url())
    report_job_ledger = PostgresReportJobLedger(_database_url())
    caller = _caller(unique_suffix)
    portfolio_ids = [
        f"PB_SG_GLOBAL_BAL_001_{unique_suffix}",
        f"PB_SG_GLOBAL_BAL_002_{unique_suffix}",
    ]
    batch = batch_ledger.create_batch(
        request=_multi_request(unique_suffix, portfolio_ids),
        caller_context=caller,
        idempotency_key=f"batch-pg-dispatch-{unique_suffix}",
    )

    result = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_batches=1000, max_active_items=1000),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=caller,
        worker_id=f"pg-worker-{unique_suffix}",
    )

    assert result.dispatched_count == 2
    assert result.back_pressure_reasons == []
    refreshed = batch_ledger.get_batch(batch.batch_id)
    assert refreshed.status == "running"
    assert [item.status for item in refreshed.items] == [
        "waiting_on_report_job",
        "waiting_on_report_job",
    ]
    assert [item.report_job_id for item in refreshed.items] == result.report_job_ids
    persisted_jobs = [report_job_ledger.get_job(job_id) for job_id in result.report_job_ids]
    assert [job.portfolio_scope["portfolio_ids"][0] for job in persisted_jobs] == portfolio_ids
    assert all(job.reporting_currency == "USD" for job in persisted_jobs)
    assert all(job.requested_output_formats == ["json", "pdf"] for job in persisted_jobs)


def test_postgres_batch_dispatch_honors_external_back_pressure_without_mutation() -> None:
    unique_suffix = uuid4().hex
    batch_ledger = PostgresReportBatchLedger(_database_url())
    report_job_ledger = PostgresReportJobLedger(_database_url())
    caller = _caller(unique_suffix)
    batch = batch_ledger.create_batch(
        request=_request(unique_suffix, f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"),
        caller_context=caller,
        idempotency_key=f"batch-pg-back-pressure-{unique_suffix}",
    )

    result = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(
            max_active_batches=1000,
            max_active_items=1000,
            max_active_render_jobs=1,
        ),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=caller,
        worker_id=f"pg-worker-{unique_suffix}",
        runtime_load=BatchRuntimeLoad(active_render_jobs=1),
    )

    assert result.dispatched_count == 0
    assert result.back_pressure_reasons == ["max_active_render_jobs_reached"]
    assert batch_ledger.get_batch(batch.batch_id).items[0].status == "materialized"


def test_postgres_batch_item_lease_expiry_and_stale_token_protection() -> None:
    unique_suffix = uuid4().hex
    batch_ledger = PostgresReportBatchLedger(_database_url())
    caller = _caller(unique_suffix)
    batch = batch_ledger.create_batch(
        request=_request(unique_suffix, f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"),
        caller_context=caller,
        idempotency_key=f"batch-pg-lease-{unique_suffix}",
    )
    t0 = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)

    assert (
        batch_ledger.acquire_dispatch_items(
            batch_id=batch.batch_id,
            worker_id=f"pg-worker-a-{unique_suffix}",
            lease_seconds=60,
            limit=0,
            now=t0,
        )
        == []
    )

    [leased] = batch_ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id=f"pg-worker-a-{unique_suffix}",
        lease_seconds=60,
        limit=1,
        now=t0,
    )
    assert leased.lease_token is not None
    assert (
        batch_ledger.acquire_dispatch_items(
            batch_id=batch.batch_id,
            worker_id=f"pg-worker-b-{unique_suffix}",
            lease_seconds=60,
            limit=1,
            now=t0 + timedelta(seconds=30),
        )
        == []
    )
    heartbeat = batch_ledger.heartbeat_item_lease(
        batch_item_id=leased.batch_item_id,
        lease_token=leased.lease_token,
        lease_seconds=120,
        now=t0 + timedelta(seconds=30),
    )
    assert heartbeat.lease_expires_at == t0 + timedelta(seconds=150)

    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        batch_ledger.mark_item_waiting_on_report_job(
            batch_item_id=leased.batch_item_id,
            lease_token="stale-token",
            report_job_id=f"rjob_stale_{unique_suffix}",
        )
    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        batch_ledger.heartbeat_item_lease(
            batch_item_id=leased.batch_item_id,
            lease_token="stale-token",
            lease_seconds=60,
            now=t0 + timedelta(seconds=40),
        )

    [takeover] = batch_ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id=f"pg-worker-b-{unique_suffix}",
        lease_seconds=60,
        limit=1,
        now=t0 + timedelta(seconds=151),
    )
    assert takeover.lease_owner == f"pg-worker-b-{unique_suffix}"
    assert takeover.lease_token != leased.lease_token


def test_postgres_batch_pause_resume_cancel_retry_and_recovery_primitives() -> None:
    unique_suffix = uuid4().hex
    batch_ledger = PostgresReportBatchLedger(_database_url())
    report_job_ledger = PostgresReportJobLedger(_database_url())
    caller = _caller(unique_suffix)
    portfolio_ids = [
        f"PB_SG_GLOBAL_BAL_001_{unique_suffix}",
        f"PB_SG_GLOBAL_BAL_002_{unique_suffix}",
        f"PB_SG_GLOBAL_BAL_003_{unique_suffix}",
    ]
    batch = batch_ledger.create_batch(
        request=_multi_request(unique_suffix, portfolio_ids),
        caller_context=caller,
        idempotency_key=f"batch-pg-control-{unique_suffix}",
    )
    t0 = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)

    paused = batch_ledger.pause_batch(batch_id=batch.batch_id, now=t0)
    blocked = ReportBatchDispatcher(
        batch_ledger=batch_ledger,
        report_job_ledger=report_job_ledger,
        policy=BatchDispatchPolicy(max_active_batches=1000, max_active_items=1000),
    ).dispatch_batch(
        batch_id=batch.batch_id,
        caller_context=caller,
        worker_id=f"pg-worker-paused-{unique_suffix}",
    )
    resumed = batch_ledger.resume_batch(batch_id=batch.batch_id, now=t0 + timedelta(seconds=1))
    [leased] = batch_ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id=f"pg-worker-lease-{unique_suffix}",
        lease_seconds=60,
        limit=1,
        now=t0 + timedelta(seconds=2),
    )
    recovered = batch_ledger.recover_expired_leases(
        batch_id=batch.batch_id,
        now=t0 + timedelta(seconds=63),
    )
    retryable = batch_ledger.mark_item_failed(
        batch_item_id=batch.items[1].batch_item_id,
        error_category="upstream_data_collection_failure",
        error_summary="transient source failure",
        retryable=True,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        next_retry_at=t0 + timedelta(seconds=64),
        now=t0 + timedelta(seconds=3),
    )
    retried = batch_ledger.retry_failed_items(
        batch_id=batch.batch_id,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        now=t0 + timedelta(seconds=64),
    )
    cancelled = batch_ledger.cancel_batch(batch_id=batch.batch_id, now=t0 + timedelta(seconds=65))
    refreshed = batch_ledger.get_batch(batch.batch_id)

    assert paused.batch_status == "paused"
    assert blocked.dispatched_count == 0
    assert resumed.batch_status == "materialized"
    assert recovered.recovered_count == 1
    assert recovered.recovery_pending_item_ids == [leased.batch_item_id]
    assert retryable.status == "failed_retryable"
    assert retried.affected_count == 1
    assert cancelled.affected_count == 3
    assert refreshed.status == "cancelled"
    assert {item.status for item in refreshed.items} == {"cancelled"}


def test_postgres_failed_single_item_batch_reconciles_terminal_and_retryable_status() -> None:
    unique_suffix = uuid4().hex
    batch_ledger = PostgresReportBatchLedger(_database_url())
    terminal_batch = batch_ledger.create_batch(
        request=_multi_request(unique_suffix, [f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"]),
        caller_context=_caller(unique_suffix),
        idempotency_key=f"batch-pg-terminal-{unique_suffix}",
    )
    retryable_suffix = uuid4().hex
    retryable_batch = batch_ledger.create_batch(
        request=_multi_request(retryable_suffix, [f"PB_SG_GLOBAL_BAL_001_{retryable_suffix}"]),
        caller_context=_caller(retryable_suffix),
        idempotency_key=f"batch-pg-retryable-{retryable_suffix}",
    )
    failed_at = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)

    terminal_item = batch_ledger.mark_item_failed(
        batch_item_id=terminal_batch.items[0].batch_item_id,
        error_category="selector_validation_failure",
        error_summary="portfolio is no longer eligible",
        retryable=False,
        now=failed_at,
    )
    retryable_item = batch_ledger.mark_item_failed(
        batch_item_id=retryable_batch.items[0].batch_item_id,
        error_category="upstream_data_collection_failure",
        error_summary="source system unavailable",
        retryable=True,
        retry_policy=BatchRetryPolicy(max_attempts=3),
        next_retry_at=failed_at + timedelta(minutes=5),
        now=failed_at,
    )

    assert terminal_item.status == "failed_terminal"
    assert batch_ledger.get_batch(terminal_batch.batch_id).status == "completed_with_failures"
    assert retryable_item.status == "failed_retryable"
    assert batch_ledger.get_batch(retryable_batch.batch_id).status == "failed"


def test_postgres_mark_item_failed_rejects_unknown_item() -> None:
    batch_ledger = PostgresReportBatchLedger(_database_url())

    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        batch_ledger.mark_item_failed(
            batch_item_id="missing",
            error_category="upstream_data_collection_failure",
            error_summary="missing item",
            retryable=True,
        )
