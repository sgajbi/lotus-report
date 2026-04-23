from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator, Mapping
from uuid import uuid4

import pytest

from app.config import settings
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_lineage.service import get_report_input_snapshot_store
from app.reporting_lineage.store import (
    ReportInputSnapshotAlreadyCapturedError,
    ReportInputSnapshotNotFoundError,
    compute_snapshot_hash,
)


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL snapshot proof")
    return database_url


def _store() -> PostgresReportInputSnapshotStore:
    return PostgresReportInputSnapshotStore(_database_url())


def _request(unique_suffix: str) -> ReportInputSnapshotCreateRequest:
    return ReportInputSnapshotCreateRequest(
        report_job_id=f"rjob_snapshot_{unique_suffix}",
        report_type="portfolio_review",
        report_data_contract_version="v1",
        portfolio_scope={"portfolio_ids": [f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"]},
        as_of_date="2026-04-22",
        snapshot_payload={
            "report_id": f"portfolio-review:PB_SG_GLOBAL_BAL_001_{unique_suffix}:2026-04-22",
            "sections": ["OVERVIEW", "PERFORMANCE"],
        },
        supportability_status="complete",
        completeness_status="complete",
        lineage_summary={"source_services": ["lotus-core", "lotus-performance", "lotus-risk"]},
        captured_at=datetime(2026, 4, 22, 9, 0, 3, tzinfo=UTC),
        correlation_id=f"corr-snapshot-{unique_suffix}",
        trace_id=f"trace-snapshot-{unique_suffix}",
    )


def test_postgres_report_input_snapshot_store_persists_and_loads_snapshot() -> None:
    store = _store()
    store.check_ready()

    request = _request(uuid4().hex)
    created = store.create_snapshot(request)

    assert created.snapshot_hash == compute_snapshot_hash(request.snapshot_payload)
    assert store.get_snapshot(created.snapshot_id).snapshot_id == created.snapshot_id
    assert store.get_snapshot_by_job(request.report_job_id).report_job_id == request.report_job_id


def test_postgres_report_input_snapshot_store_rejects_conflicting_rewrite() -> None:
    store = _store()
    unique_suffix = uuid4().hex
    request = _request(unique_suffix)
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
        match="report_input_snapshot_schema_missing:report_input_snapshot",
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
