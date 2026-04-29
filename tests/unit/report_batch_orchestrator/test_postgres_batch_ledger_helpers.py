from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator, Mapping

from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger


class _Cursor:
    def __init__(self, rows: list[Mapping[str, Any]]):
        self._rows = rows

    def fetchall(self) -> list[Mapping[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[Mapping[str, Any]]):
        self.rows = rows
        self.query: str | None = None
        self.params: tuple[Any, ...] | None = None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.query = query
        self.params = params
        return _Cursor(self.rows)


def test_postgres_batch_ledger_attention_batch_scan_builds_source_backed_query() -> None:
    ledger = object.__new__(PostgresReportBatchLedger)
    connection = _Connection([{"batch_id": "rbch_attention"}])

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield connection

    ledger._connect = _connect  # type: ignore[method-assign]

    batch_ids = ledger.list_attention_batch_ids(
        limit=25,
        now=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    )

    assert batch_ids == ["rbch_attention"]
    assert connection.query is not None
    assert "report_batch.status IN ('materialized', 'running')" in connection.query
    assert "'leased'" in connection.query
    assert "'waiting_on_report_job'" in connection.query
    assert "report_batch_item.retry_eligible IS TRUE" in connection.query
    assert "report_batch_item.next_retry_at <= %s" in connection.query
    assert connection.params == (datetime(2026, 4, 28, 12, 0, tzinfo=UTC), 25)


def test_postgres_batch_ledger_attention_batch_scan_rejects_non_positive_limit() -> None:
    ledger = object.__new__(PostgresReportBatchLedger)
    connection = _Connection([{"batch_id": "rbch_attention"}])

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield connection

    ledger._connect = _connect  # type: ignore[method-assign]

    assert ledger.list_attention_batch_ids(limit=0) == []
    assert connection.query is None
