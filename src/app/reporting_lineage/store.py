from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from app.reporting_lineage.models import ReportInputSnapshotCreateRequest, ReportInputSnapshotRecord

POSTURE_VALUES = ("complete", "partial", "unavailable", "not_supported", "redacted", "error")


class ReportInputSnapshotNotFoundError(RuntimeError):
    """Raised when the requested report input snapshot does not exist."""


class ReportInputSnapshotAlreadyCapturedError(RuntimeError):
    """Raised when a conflicting immutable snapshot already exists for the job."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(
        _normalize_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_snapshot_hash(snapshot_payload: Mapping[str, Any] | dict[str, Any]) -> str:
    payload = canonical_json_dumps(dict(snapshot_payload)).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json_value(item) for item in value]
    return value


def _dt_to_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dt_from_text(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _date_to_text(value: date) -> str:
    return value.isoformat()


def _date_from_value(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _record_from_row(row: Mapping[str, Any]) -> ReportInputSnapshotRecord:
    payload = row["snapshot_payload_json"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    summary = row["lineage_summary_json"]
    if isinstance(summary, str):
        summary = json.loads(summary)
    scope = row["portfolio_scope_json"]
    if isinstance(scope, str):
        scope = json.loads(scope)
    return ReportInputSnapshotRecord(
        snapshot_id=str(row["snapshot_id"]),
        report_job_id=str(row["report_job_id"]),
        report_type=str(row["report_type"]),
        report_data_contract_version=str(row["report_data_contract_version"]),
        portfolio_scope=dict(scope),
        as_of_date=_date_from_value(row["as_of_date"]),
        snapshot_payload=dict(payload),
        snapshot_hash=str(row["snapshot_hash"]),
        snapshot_storage_ref=(
            str(row["snapshot_storage_ref"]) if row["snapshot_storage_ref"] else None
        ),
        supportability_status=str(row["supportability_status"]),
        completeness_status=str(row["completeness_status"]),
        lineage_summary=dict(summary),
        captured_at=_dt_from_text(row["captured_at"]),
        created_at=_dt_from_text(row["created_at"]),
        correlation_id=str(row["correlation_id"]),
        trace_id=str(row["trace_id"]),
    )


class ReportInputSnapshotStore:
    """SQLite-backed unit-test adapter for durable report input snapshots."""

    def __init__(self, database_path: Path):
        self._database_path = database_path
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_input_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    report_job_id TEXT NOT NULL UNIQUE,
                    report_type TEXT NOT NULL,
                    report_data_contract_version TEXT NOT NULL,
                    portfolio_scope_json TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    snapshot_payload_json TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    snapshot_storage_ref TEXT,
                    supportability_status TEXT NOT NULL,
                    completeness_status TEXT NOT NULL,
                    lineage_summary_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_input_snapshot_created
                ON report_input_snapshot(created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_input_snapshot_supportability
                ON report_input_snapshot(supportability_status)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_input_snapshot_report_type_created
                ON report_input_snapshot(report_type, created_at)
                """
            )

    def create_snapshot(
        self, request: ReportInputSnapshotCreateRequest
    ) -> ReportInputSnapshotRecord:
        snapshot_hash = compute_snapshot_hash(request.snapshot_payload)
        with self._connect() as connection:
            existing_row = connection.execute(
                """
                SELECT *
                FROM report_input_snapshot
                WHERE report_job_id = ?
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    request.report_job_id,
                    request.report_type,
                    request.report_data_contract_version,
                    canonical_json_dumps(request.portfolio_scope),
                    _date_to_text(request.as_of_date),
                    canonical_json_dumps(request.snapshot_payload),
                    snapshot_hash,
                    request.snapshot_storage_ref,
                    request.supportability_status,
                    request.completeness_status,
                    canonical_json_dumps(request.lineage_summary),
                    _dt_to_text(request.captured_at),
                    _dt_to_text(now),
                    request.correlation_id,
                    request.trace_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM report_input_snapshot WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        assert row is not None
        return _record_from_row(row)

    def get_snapshot(self, snapshot_id: str) -> ReportInputSnapshotRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_input_snapshot WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            raise ReportInputSnapshotNotFoundError("report_input_snapshot_not_found")
        return _record_from_row(row)

    def get_snapshot_by_job(self, report_job_id: str) -> ReportInputSnapshotRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_input_snapshot WHERE report_job_id = ?",
                (report_job_id,),
            ).fetchone()
        if not row:
            raise ReportInputSnapshotNotFoundError("report_input_snapshot_not_found")
        return _record_from_row(row)
