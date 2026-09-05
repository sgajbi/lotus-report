from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from uuid import uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.postgres import PostgresConnectionProvider
from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportInputSnapshotRecord,
    ReportUpstreamCallCreateRequest,
    ReportUpstreamCallRecord,
)
from app.reporting_lineage.store import (
    ReportInputSnapshotAlreadyCapturedError,
    ReportInputSnapshotLineageConflictError,
    ReportInputSnapshotNotFoundError,
    _normalize_json_value,
    _record_from_row,
    _snapshot_lineage_matches,
    _upstream_call_from_row,
    _upstream_calls_match,
    compute_snapshot_hash,
    utc_now,
)
from app.reporting_persistence import ManagedPostgresAdapter, apply_report_schema_migrations


class PostgresReportInputSnapshotStore(ManagedPostgresAdapter):
    """PostgreSQL-backed runtime store for durable report input snapshots."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connection_provider: PostgresConnectionProvider | None = None,
    ) -> None:
        if connection_provider is None:
            if database_url is None:
                raise ValueError("report_input_snapshot_store_database_url_required")
            connection_provider = PostgresConnectionProvider(database_url=database_url)
            self._owns_connection_provider = True
        else:
            self._owns_connection_provider = False
        self._connection_provider = connection_provider
        self.ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[Connection[Mapping[str, Any]]]:
        with self._connection_provider.connection() as connection:
            yield connection

    def close(self) -> None:
        if self._owns_connection_provider:
            self._connection_provider.close()

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            apply_report_schema_migrations(connection)

    def check_ready(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('report_input_snapshot', 'report_upstream_call')
                """
            ).fetchall()
        present = {str(row["table_name"]) for row in rows}
        missing = {"report_input_snapshot", "report_upstream_call"} - present
        if missing:
            raise RuntimeError(f"report_input_snapshot_schema_missing:{','.join(sorted(missing))}")

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
        connection: Connection[Mapping[str, Any]],
        request: ReportInputSnapshotCreateRequest,
    ) -> ReportInputSnapshotRecord:
        snapshot_hash = compute_snapshot_hash(request.snapshot_payload)
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
                    source_revision_vector_json, source_cut_coherence_json,
                    supportability_status, completeness_status,
                    lineage_summary_json, captured_at, created_at, correlation_id, trace_id
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
            (
                snapshot_id,
                request.report_job_id,
                request.report_type,
                request.report_data_contract_version,
                Jsonb(_normalize_json_value(request.portfolio_scope)),
                request.as_of_date,
                Jsonb(_normalize_json_value(request.snapshot_payload)),
                snapshot_hash,
                request.snapshot_storage_ref,
                request.report_revision_id,
                request.series_digest,
                request.source_revision_digest,
                request.factual_content_digest,
                request.factual_boundary_version,
                (
                    Jsonb(_normalize_json_value(request.source_revision_vector))
                    if request.source_revision_vector is not None
                    else None
                ),
                (
                    Jsonb(_normalize_json_value(request.source_cut_coherence))
                    if request.source_cut_coherence is not None
                    else None
                ),
                request.supportability_status,
                request.completeness_status,
                Jsonb(_normalize_json_value(request.lineage_summary)),
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

    def _repair_snapshot_lineage_metadata(
        self,
        connection: Connection[Mapping[str, Any]],
        *,
        snapshot_id: str,
        request: ReportInputSnapshotCreateRequest,
    ) -> ReportInputSnapshotRecord:
        connection.execute(
            """
            UPDATE report_input_snapshot
            SET supportability_status = %s,
                completeness_status = %s,
                lineage_summary_json = %s
            WHERE snapshot_id = %s
            """,
            (
                request.supportability_status,
                request.completeness_status,
                Jsonb(_normalize_json_value(request.lineage_summary)),
                snapshot_id,
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
                "SELECT 1 FROM report_input_snapshot WHERE snapshot_id = %s",
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
        connection: Connection[Mapping[str, Any]],
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
            WHERE snapshot_id = %s
            ORDER BY created_at ASC, upstream_call_id ASC
            """,
            (snapshot_id,),
        ).fetchall()
        return [_upstream_call_from_row(row) for row in rows]

    def _list_upstream_calls(
        self,
        connection: Connection[Mapping[str, Any]],
        snapshot_id: str,
    ) -> list[ReportUpstreamCallRecord]:
        rows = connection.execute(
            """
            SELECT *
            FROM report_upstream_call
            WHERE snapshot_id = %s
            ORDER BY created_at ASC, upstream_call_id ASC
            """,
            (snapshot_id,),
        ).fetchall()
        return [_upstream_call_from_row(row) for row in rows]

    def _insert_upstream_calls(
        self,
        connection: Connection[Mapping[str, Any]],
        *,
        snapshot_id: str,
        calls: list[ReportUpstreamCallCreateRequest],
    ) -> None:
        now = utc_now()
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
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    )
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
                    call.captured_at,
                    now,
                ),
            )

    def list_upstream_calls(self, snapshot_id: str) -> list[ReportUpstreamCallRecord]:
        with self._connect() as connection:
            return self._list_upstream_calls(connection, snapshot_id)

    def list_upstream_calls_by_job(self, report_job_id: str) -> list[ReportUpstreamCallRecord]:
        snapshot = self.get_snapshot_by_job(report_job_id)
        return self.list_upstream_calls(snapshot.snapshot_id)
