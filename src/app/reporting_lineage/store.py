from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportInputSnapshotRecord,
    ReportUpstreamCallCreateRequest,
    ReportUpstreamCallRecord,
)

POSTURE_VALUES = ("complete", "partial", "unavailable", "not_supported", "redacted", "error")


class ReportInputSnapshotNotFoundError(RuntimeError):
    """Raised when the requested report input snapshot does not exist."""


class ReportInputSnapshotAlreadyCapturedError(RuntimeError):
    """Raised when a conflicting immutable snapshot already exists for the job."""


class ReportInputSnapshotLineageNotFoundError(RuntimeError):
    """Raised when requested upstream lineage evidence does not exist."""


class ReportInputSnapshotLineageConflictError(RuntimeError):
    """Raised when immutable upstream lineage differs from the captured evidence."""


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
    vector = row["source_revision_vector_json"]
    if isinstance(vector, str):
        vector = json.loads(vector)
    coherence = row["source_cut_coherence_json"]
    if isinstance(coherence, str):
        coherence = json.loads(coherence)
    lifecycle = row["lifecycle_json"]
    if isinstance(lifecycle, str):
        lifecycle = json.loads(lifecycle)
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
        report_revision_id=(str(row["report_revision_id"]) if row["report_revision_id"] else None),
        series_digest=str(row["series_digest"]) if row["series_digest"] else None,
        source_revision_digest=(
            str(row["source_revision_digest"]) if row["source_revision_digest"] else None
        ),
        factual_content_digest=(
            str(row["factual_content_digest"]) if row["factual_content_digest"] else None
        ),
        factual_boundary_version=(
            str(row["factual_boundary_version"]) if row["factual_boundary_version"] else None
        ),
        source_revision_vector=dict(vector) if isinstance(vector, dict) else None,
        source_cut_coherence=dict(coherence) if isinstance(coherence, dict) else None,
        lifecycle=dict(lifecycle) if isinstance(lifecycle, dict) else None,
        supportability_status=str(row["supportability_status"]),
        completeness_status=str(row["completeness_status"]),
        lineage_summary=dict(summary),
        captured_at=_dt_from_text(row["captured_at"]),
        created_at=_dt_from_text(row["created_at"]),
        correlation_id=str(row["correlation_id"]),
        trace_id=str(row["trace_id"]),
    )


def _upstream_call_from_row(row: Mapping[str, Any]) -> ReportUpstreamCallRecord:
    return ReportUpstreamCallRecord(
        upstream_call_id=str(row["upstream_call_id"]),
        snapshot_id=str(row["snapshot_id"]),
        service_name=str(row["service_name"]),
        endpoint=str(row["endpoint"]),
        method=str(row["method"]),
        contract_version=str(row["contract_version"]),
        request_hash=str(row["request_hash"]),
        response_hash=str(row["response_hash"]) if row["response_hash"] else None,
        response_ref=str(row["response_ref"]) if row["response_ref"] else None,
        status_code=int(row["status_code"]),
        latency_ms=int(row["latency_ms"]),
        supportability_status=str(row["supportability_status"]),
        completeness_status=str(row["completeness_status"]),
        failure_category=str(row["failure_category"]),
        failure_message=str(row["failure_message"]) if row["failure_message"] else None,
        captured_at=_dt_from_text(row["captured_at"]),
        created_at=_dt_from_text(row["created_at"]),
        correlation_id=str(row["correlation_id"]),
        trace_id=str(row["trace_id"]),
    )


def _upstream_call_signature(
    call: ReportUpstreamCallCreateRequest | ReportUpstreamCallRecord,
) -> tuple[Any, ...]:
    """Return the immutable business identity of an upstream call.

    Runtime latency and timestamps are deliberately excluded: they describe the
    capture attempt, while the remaining fields identify the source evidence.
    """

    return (
        call.service_name,
        call.endpoint,
        call.method,
        call.contract_version,
        call.request_hash,
        call.response_hash,
        call.response_ref,
        call.status_code,
        call.supportability_status,
        call.completeness_status,
        call.failure_category,
        call.failure_message,
        call.correlation_id,
        call.trace_id,
    )


def _upstream_calls_match(
    existing: list[ReportUpstreamCallRecord],
    requested: list[ReportUpstreamCallCreateRequest],
) -> bool:
    return Counter(map(_upstream_call_signature, existing)) == Counter(
        map(_upstream_call_signature, requested)
    )


def _snapshot_lineage_matches(
    existing: ReportInputSnapshotRecord,
    requested: ReportInputSnapshotCreateRequest,
) -> bool:
    return (
        existing.supportability_status == requested.supportability_status
        and existing.completeness_status == requested.completeness_status
        and canonical_json_dumps(existing.lineage_summary)
        == canonical_json_dumps(requested.lineage_summary)
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
                    report_revision_id TEXT,
                    series_digest TEXT,
                    source_revision_digest TEXT,
                    factual_content_digest TEXT,
                    factual_boundary_version TEXT,
                    source_revision_vector_json TEXT,
                    source_cut_coherence_json TEXT,
                    lifecycle_json TEXT,
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
            # Revision columns arrived after the table (report#283); a
            # database created before them evolves additively in place.
            existing_columns = {
                str(column_row["name"])
                for column_row in connection.execute(
                    "PRAGMA table_info(report_input_snapshot)"
                ).fetchall()
            }
            for column_name in (
                "report_revision_id",
                "series_digest",
                "source_revision_digest",
                "factual_content_digest",
                "factual_boundary_version",
                "source_revision_vector_json",
                "source_cut_coherence_json",
                "lifecycle_json",
            ):
                if column_name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE report_input_snapshot ADD COLUMN {column_name} TEXT"
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
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_input_snapshot_revision
                ON report_input_snapshot(report_revision_id)
                """
            )
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS report_upstream_call (
                    upstream_call_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    method TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_hash TEXT,
                    response_ref TEXT,
                    status_code INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    supportability_status TEXT NOT NULL
                        CHECK (supportability_status IN {POSTURE_VALUES}),
                    completeness_status TEXT NOT NULL
                        CHECK (completeness_status IN {POSTURE_VALUES}),
                    failure_category TEXT NOT NULL,
                    failure_message TEXT,
                    correlation_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES report_input_snapshot(snapshot_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_upstream_call_snapshot
                ON report_upstream_call(snapshot_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_upstream_call_service_endpoint
                ON report_upstream_call(service_name, endpoint)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_upstream_call_supportability
                ON report_upstream_call(supportability_status)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_upstream_call_created
                ON report_upstream_call(created_at)
                """
            )

    def create_snapshot(
        self, request: ReportInputSnapshotCreateRequest
    ) -> ReportInputSnapshotRecord:
        with self._connect() as connection:
            return self._get_or_create_snapshot(connection, request)

    def create_capture(
        self,
        *,
        snapshot: ReportInputSnapshotCreateRequest,
        upstream_calls: list[ReportUpstreamCallCreateRequest],
    ) -> tuple[ReportInputSnapshotRecord, list[ReportUpstreamCallRecord]]:
        """Persist a snapshot and its complete source-call ledger atomically."""

        with self._connect() as connection:
            snapshot_record = self._get_or_create_snapshot(connection, snapshot)
            existing_calls = self._list_upstream_calls(connection, snapshot_record.snapshot_id)
            if existing_calls and not _snapshot_lineage_matches(snapshot_record, snapshot):
                raise ReportInputSnapshotLineageConflictError(
                    "report_input_snapshot_lineage_summary_conflict"
                )
            if (
                not existing_calls
                and upstream_calls
                and not _snapshot_lineage_matches(snapshot_record, snapshot)
            ):
                snapshot_record = self._repair_snapshot_lineage_metadata(
                    connection,
                    snapshot_id=snapshot_record.snapshot_id,
                    request=snapshot,
                )
            call_records = self._get_or_create_upstream_calls(
                connection,
                snapshot_id=snapshot_record.snapshot_id,
                calls=upstream_calls,
            )
            return snapshot_record, call_records

    def _get_or_create_snapshot(
        self,
        connection: sqlite3.Connection,
        request: ReportInputSnapshotCreateRequest,
    ) -> ReportInputSnapshotRecord:
        snapshot_hash = compute_snapshot_hash(request.snapshot_payload)
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
            raise ReportInputSnapshotAlreadyCapturedError("report_input_snapshot_already_captured")

        now = utc_now()
        snapshot_id = f"rsnap_{uuid4().hex}"
        connection.execute(
            """
                INSERT INTO report_input_snapshot (
                    snapshot_id, report_job_id, report_type, report_data_contract_version,
                    portfolio_scope_json, as_of_date, snapshot_payload_json, snapshot_hash,
                    snapshot_storage_ref, report_revision_id, series_digest,
                    source_revision_digest, factual_content_digest, factual_boundary_version,
                    source_revision_vector_json, source_cut_coherence_json, lifecycle_json,
                    supportability_status, completeness_status,
                    lineage_summary_json, captured_at, created_at, correlation_id, trace_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                request.report_revision_id,
                request.series_digest,
                request.source_revision_digest,
                request.factual_content_digest,
                request.factual_boundary_version,
                (
                    canonical_json_dumps(request.source_revision_vector)
                    if request.source_revision_vector is not None
                    else None
                ),
                (
                    canonical_json_dumps(request.source_cut_coherence)
                    if request.source_cut_coherence is not None
                    else None
                ),
                (
                    canonical_json_dumps(request.lifecycle)
                    if request.lifecycle is not None
                    else None
                ),
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

    def _repair_snapshot_lineage_metadata(
        self,
        connection: sqlite3.Connection,
        *,
        snapshot_id: str,
        request: ReportInputSnapshotCreateRequest,
    ) -> ReportInputSnapshotRecord:
        connection.execute(
            """
            UPDATE report_input_snapshot
            SET supportability_status = ?,
                completeness_status = ?,
                lineage_summary_json = ?
            WHERE snapshot_id = ?
            """,
            (
                request.supportability_status,
                request.completeness_status,
                canonical_json_dumps(request.lineage_summary),
                snapshot_id,
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

    def create_upstream_calls(
        self,
        *,
        snapshot_id: str,
        calls: list[ReportUpstreamCallCreateRequest],
    ) -> list[ReportUpstreamCallRecord]:
        if not calls:
            return []
        with self._connect() as connection:
            snapshot_exists = connection.execute(
                "SELECT 1 FROM report_input_snapshot WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if not snapshot_exists:
                raise ReportInputSnapshotNotFoundError("report_input_snapshot_not_found")
            return self._get_or_create_upstream_calls(
                connection,
                snapshot_id=snapshot_id,
                calls=calls,
            )

    def _get_or_create_upstream_calls(
        self,
        connection: sqlite3.Connection,
        *,
        snapshot_id: str,
        calls: list[ReportUpstreamCallCreateRequest],
    ) -> list[ReportUpstreamCallRecord]:
        existing = self._list_upstream_calls(connection, snapshot_id)
        if existing:
            if not _upstream_calls_match(existing, calls):
                raise ReportInputSnapshotLineageConflictError(
                    "report_input_snapshot_lineage_conflict"
                )
            return existing
        if not calls:
            return []
        self._insert_upstream_calls(connection, snapshot_id=snapshot_id, calls=calls)
        rows = connection.execute(
            """
            SELECT *
            FROM report_upstream_call
            WHERE snapshot_id = ?
            ORDER BY created_at ASC, upstream_call_id ASC
            """,
            (snapshot_id,),
        ).fetchall()
        return [_upstream_call_from_row(row) for row in rows]

    def _list_upstream_calls(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
    ) -> list[ReportUpstreamCallRecord]:
        rows = connection.execute(
            """
            SELECT *
            FROM report_upstream_call
            WHERE snapshot_id = ?
            ORDER BY created_at ASC, upstream_call_id ASC
            """,
            (snapshot_id,),
        ).fetchall()
        return [_upstream_call_from_row(row) for row in rows]

    def _insert_upstream_calls(
        self,
        connection: sqlite3.Connection,
        *,
        snapshot_id: str,
        calls: list[ReportUpstreamCallCreateRequest],
    ) -> None:
        created_at = _dt_to_text(utc_now())
        for call in calls:
            connection.execute(
                """
                    INSERT INTO report_upstream_call (
                        upstream_call_id, snapshot_id, service_name, endpoint, method,
                        contract_version, request_hash, response_hash, response_ref,
                        status_code, latency_ms, supportability_status, completeness_status,
                        failure_category, failure_message, correlation_id, trace_id,
                        captured_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    f"ruc_{uuid4().hex}",
                    snapshot_id,
                    call.service_name,
                    call.endpoint,
                    call.method,
                    call.contract_version,
                    call.request_hash,
                    call.response_hash,
                    call.response_ref,
                    call.status_code,
                    call.latency_ms,
                    call.supportability_status,
                    call.completeness_status,
                    call.failure_category,
                    call.failure_message,
                    call.correlation_id,
                    call.trace_id,
                    _dt_to_text(call.captured_at),
                    created_at,
                ),
            )

    def list_upstream_calls(self, snapshot_id: str) -> list[ReportUpstreamCallRecord]:
        with self._connect() as connection:
            return self._list_upstream_calls(connection, snapshot_id)

    def list_upstream_calls_by_job(self, report_job_id: str) -> list[ReportUpstreamCallRecord]:
        snapshot = self.get_snapshot_by_job(report_job_id)
        return self.list_upstream_calls(snapshot.snapshot_id)
