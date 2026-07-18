from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator, Mapping
from uuid import uuid4

import pytest

from app.config import settings
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportUpstreamCallCreateRequest,
)
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_lineage.service import get_report_input_snapshot_store
from app.reporting_lineage.store import (
    ReportInputSnapshotAlreadyCapturedError,
    ReportInputSnapshotNotFoundError,
    compute_snapshot_hash,
)
from tests.integration.postgres_adapter_ownership import own_postgres_adapter


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL snapshot proof")
    return database_url


def _store() -> PostgresReportInputSnapshotStore:
    return own_postgres_adapter(PostgresReportInputSnapshotStore(_database_url()))


def _seed_job(unique_suffix: str) -> str:
    ledger = own_postgres_adapter(PostgresReportJobLedger(_database_url()))
    job = ledger.create_portfolio_review_job(
        request=PortfolioReviewJobRequest(
            portfolio_scope={"portfolio_ids": [f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"]},
            as_of_date="2026-04-22",
            requested_output_formats=["json"],
            reporting_currency="USD",
            options={"sections": ["OVERVIEW", "PERFORMANCE"]},
        ),
        caller_context=ReportCallerContext(
            triggered_by="advisor-123",
            caller_application="lotus-gateway",
            tenant_id="tenant-sg",
            region="APAC",
            correlation_id=f"corr-snapshot-{unique_suffix}",
            trace_id=f"trace-snapshot-{unique_suffix}",
        ),
        idempotency_key=f"snapshot-proof-{unique_suffix}",
    )
    return job.job_id


def _request(
    unique_suffix: str,
    *,
    report_job_id: str,
    **overrides: Any,
) -> ReportInputSnapshotCreateRequest:
    payload: dict[str, Any] = {
        "report_job_id": report_job_id,
        "report_type": "portfolio_review",
        "report_data_contract_version": "v1",
        "portfolio_scope": {"portfolio_ids": [f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"]},
        "as_of_date": "2026-04-22",
        "snapshot_payload": {
            "report_id": f"portfolio-review:PB_SG_GLOBAL_BAL_001_{unique_suffix}:2026-04-22",
            "sections": ["OVERVIEW", "PERFORMANCE"],
        },
        "supportability_status": "complete",
        "completeness_status": "complete",
        "lineage_summary": {"source_services": ["lotus-core", "lotus-performance", "lotus-risk"]},
        "captured_at": datetime(2026, 4, 22, 9, 0, 3, tzinfo=UTC),
        "correlation_id": f"corr-snapshot-{unique_suffix}",
        "trace_id": f"trace-snapshot-{unique_suffix}",
    }
    payload.update(overrides)
    return ReportInputSnapshotCreateRequest(**payload)


def test_postgres_report_input_snapshot_store_persists_and_loads_snapshot() -> None:
    store = _store()
    store.check_ready()

    unique_suffix = uuid4().hex
    request = _request(unique_suffix, report_job_id=_seed_job(unique_suffix))
    created = store.create_snapshot(request)

    assert created.snapshot_hash == compute_snapshot_hash(request.snapshot_payload)
    assert store.get_snapshot(created.snapshot_id).snapshot_id == created.snapshot_id
    assert store.get_snapshot_by_job(request.report_job_id).report_job_id == request.report_job_id


def test_postgres_report_input_snapshot_store_persists_and_lists_upstream_calls() -> None:
    store = _store()
    unique_suffix = uuid4().hex
    snapshot = store.create_snapshot(
        _request(unique_suffix, report_job_id=_seed_job(unique_suffix))
    )

    created = store.create_upstream_calls(
        snapshot_id=snapshot.snapshot_id,
        calls=[
            ReportUpstreamCallCreateRequest(
                service_name="lotus-core",
                endpoint="/reporting/portfolio-summary/query",
                method="POST",
                contract_version="v1",
                request_hash="sha256:req",
                response_hash="sha256:resp",
                response_ref=None,
                status_code=200,
                latency_ms=184,
                supportability_status="complete",
                completeness_status="complete",
                failure_category="none",
                failure_message=None,
                captured_at=datetime(2026, 4, 22, 9, 0, 4, tzinfo=UTC),
                correlation_id="corr-lineage",
                trace_id="trace-lineage",
            )
        ],
    )

    assert len(created) == 1
    assert store.list_upstream_calls(snapshot.snapshot_id)[0].service_name == "lotus-core"
    assert store.list_upstream_calls_by_job(snapshot.report_job_id)[0].status_code == 200


def test_postgres_report_input_snapshot_store_rejects_conflicting_rewrite() -> None:
    store = _store()
    unique_suffix = uuid4().hex
    request = _request(unique_suffix, report_job_id=_seed_job(unique_suffix))
    store.create_snapshot(request)

    with pytest.raises(
        ReportInputSnapshotAlreadyCapturedError,
        match="report_input_snapshot_already_captured",
    ):
        store.create_snapshot(
            request.model_copy(
                update={"snapshot_payload": {"report_id": "portfolio-review:changed"}}
            )
        )


def test_postgres_report_input_snapshot_store_normalizes_datetime_payloads() -> None:
    store = _store()
    unique_suffix = uuid4().hex
    created = store.create_snapshot(
        _request(
            unique_suffix,
            report_job_id=_seed_job(unique_suffix),
            snapshot_payload={
                "captured_window": {
                    "started_at": datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
                    "ended_at": datetime(2026, 4, 22, 9, 5, tzinfo=UTC),
                }
            },
            lineage_summary={
                "last_source_refresh_at": datetime(2026, 4, 22, 8, 59, tzinfo=UTC),
            },
        )
    )

    assert created.snapshot_payload["captured_window"]["started_at"] == "2026-04-22T09:00:00Z"
    assert created.snapshot_payload["captured_window"]["ended_at"] == "2026-04-22T09:05:00Z"
    assert created.lineage_summary["last_source_refresh_at"] == "2026-04-22T08:59:00Z"


def test_postgres_report_input_snapshot_store_check_ready_reports_missing_schema() -> None:
    store = object.__new__(PostgresReportInputSnapshotStore)

    class _Cursor:
        def fetchall(self) -> list[Mapping[str, Any]]:
            return []

    class _Connection:
        def execute(self, *_args: object, **_kwargs: object) -> _Cursor:
            return _Cursor()

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield _Connection()

    store._connect = _connect  # type: ignore[method-assign]

    with pytest.raises(
        RuntimeError,
        match="report_input_snapshot_schema_missing:report_input_snapshot,report_upstream_call",
    ):
        store.check_ready()


def test_postgres_report_input_snapshot_store_service_returns_postgres_store() -> None:
    settings.report_job_ledger_database_url = _database_url()
    get_report_input_snapshot_store.cache_clear()
    try:
        store = get_report_input_snapshot_store()
        store.check_ready()
        assert isinstance(store, PostgresReportInputSnapshotStore)
    finally:
        get_report_input_snapshot_store.cache_clear()


def test_postgres_report_input_snapshot_store_reports_missing_snapshot() -> None:
    with pytest.raises(ReportInputSnapshotNotFoundError, match="report_input_snapshot_not_found"):
        _store().get_snapshot(f"rsnap_missing_{uuid4().hex}")
