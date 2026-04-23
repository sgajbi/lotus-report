from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.reporting_lineage.models import ReportInputSnapshotCreateRequest, ReportInputSnapshotRecord
from app.reporting_lineage.store import (
    ReportInputSnapshotAlreadyCapturedError,
    ReportInputSnapshotNotFoundError,
    _record_from_row,
    compute_snapshot_hash,
    utc_now,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


class PostgresReportInputSnapshotStore:
    """PostgreSQL-backed runtime store for durable report input snapshots."""

    def __init__(self, database_url: str):
        self._database_url = database_url
        self.ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[Connection[Mapping[str, Any]]]:
        connection = psycopg.connect(self._database_url, row_factory=dict_row)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                schema = migration_path.read_text(encoding="utf-8")
                for statement in schema.split(";"):
                    if statement.strip():
                        connection.execute(statement)

    def check_ready(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('report_input_snapshot')
                """
            ).fetchall()
        present = {str(row["table_name"]) for row in rows}
        missing = {"report_input_snapshot"} - present
        if missing:
            raise RuntimeError(f"report_input_snapshot_schema_missing:{','.join(sorted(missing))}")

    def create_snapshot(
        self, request: ReportInputSnapshotCreateRequest
    ) -> ReportInputSnapshotRecord:
        snapshot_hash = compute_snapshot_hash(request.snapshot_payload)
        with self._connect() as connection:
            existing_row = connection.execute(
                """
                SELECT *
                FROM report_input_snapshot
                WHERE report_job_id = %s
                """,
                (request.report_job_id,),
            ).fetchone()
            if existing_row:
                existing = _record_from_row(existing_row)
                if existing.snapshot_hash == snapshot_hash:
                    return existing
                raise ReportInputSnapshotAlreadyCapturedError(
                    "report_input_snapshot_already_captured"
                )

            now = utc_now()
            snapshot_id = f"rsnap_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO report_input_snapshot (
                    snapshot_id, report_job_id, report_type, report_data_contract_version,
                    portfolio_scope_json, as_of_date, snapshot_payload_json, snapshot_hash,
                    snapshot_storage_ref, supportability_status, completeness_status,
                    lineage_summary_json, captured_at, created_at, correlation_id, trace_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot_id,
                    request.report_job_id,
                    request.report_type,
                    request.report_data_contract_version,
                    Jsonb(request.portfolio_scope),
                    request.as_of_date,
                    Jsonb(request.snapshot_payload),
                    snapshot_hash,
                    request.snapshot_storage_ref,
                    request.supportability_status,
                    request.completeness_status,
                    Jsonb(request.lineage_summary),
                    request.captured_at,
                    now,
                    request.correlation_id,
                    request.trace_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM report_input_snapshot WHERE snapshot_id = %s",
                (snapshot_id,),
            ).fetchone()
        assert row is not None
        return _record_from_row(row)

    def get_snapshot(self, snapshot_id: str) -> ReportInputSnapshotRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_input_snapshot WHERE snapshot_id = %s",
                (snapshot_id,),
            ).fetchone()
        if not row:
            raise ReportInputSnapshotNotFoundError("report_input_snapshot_not_found")
        return _record_from_row(row)

    def get_snapshot_by_job(self, report_job_id: str) -> ReportInputSnapshotRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_input_snapshot WHERE report_job_id = %s",
                (report_job_id,),
            ).fetchone()
        if not row:
            raise ReportInputSnapshotNotFoundError("report_input_snapshot_not_found")
        return _record_from_row(row)
