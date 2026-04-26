from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

import psycopg
from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.report_batch_orchestrator.ledger import (
    BatchIdempotencyConflictError,
    MissingBatchIdempotencyKeyError,
    compute_batch_request_hash,
    utc_now,
)
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    MaterializedPortfolio,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.selector import materialize_portfolios
from app.reporting_jobs.models import ReportCallerContext

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


class PostgresReportBatchLedger:
    """PostgreSQL-backed durable ledger for batch and batch-item materialization."""

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
                  AND table_name IN ('report_batch', 'report_batch_item')
                """
            ).fetchall()
            present = {str(row["table_name"]) for row in rows}
            missing = {"report_batch", "report_batch_item"} - present
            if missing:
                missing_names = ",".join(sorted(missing))
                raise RuntimeError(f"report_batch_ledger_schema_missing:{missing_names}")

    def create_batch(
        self,
        *,
        request: BatchCreateRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportBatchRecord:
        if not idempotency_key or not idempotency_key.strip():
            raise MissingBatchIdempotencyKeyError("missing_batch_idempotency_key")

        normalized_key = idempotency_key.strip()
        materialized = materialize_portfolios(request=request, caller_context=caller_context)
        request_hash = compute_batch_request_hash(
            request=request,
            caller_context=caller_context,
            materialized_portfolios=materialized,
        )

        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT batch_id, request_hash
                    FROM report_batch
                    WHERE idempotency_key = %s
                    """,
                    (normalized_key,),
                ).fetchone()
                if existing:
                    return self._existing_or_conflict(connection, existing, request_hash)

                now = utc_now()
                batch_id = f"rbch_{uuid4().hex}"
                materialized_ids = [portfolio.portfolio_id for portfolio in materialized]
                connection.execute(
                    """
                    INSERT INTO report_batch (
                        batch_id, selector_mode, tenant_id, region,
                        materialized_portfolio_ids_json, requested_output_formats_json,
                        as_of_date, reporting_currency, options_json, trigger_type,
                        triggered_by, caller_application, booking_center_code, role,
                        idempotency_key, request_hash, status, item_count,
                        correlation_id, trace_id, created_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        batch_id,
                        request.selector_mode,
                        caller_context.tenant_id,
                        caller_context.region,
                        Jsonb(materialized_ids),
                        Jsonb(sorted(request.requested_output_formats)),
                        request.as_of_date,
                        request.reporting_currency,
                        Jsonb(request.options),
                        caller_context.trigger_type,
                        caller_context.triggered_by,
                        caller_context.caller_application,
                        caller_context.booking_center_code,
                        caller_context.role,
                        normalized_key,
                        request_hash,
                        "materialized",
                        len(materialized),
                        caller_context.correlation_id,
                        caller_context.trace_id,
                        now,
                    ),
                )
                self._insert_items(
                    connection=connection,
                    batch_id=batch_id,
                    idempotency_key=normalized_key,
                    request=request,
                    materialized=materialized,
                    created_at=now,
                )
                return self._load_batch(connection, batch_id)
        except UniqueViolation as exc:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT batch_id, request_hash
                    FROM report_batch
                    WHERE idempotency_key = %s
                    """,
                    (normalized_key,),
                ).fetchone()
                if existing:
                    return self._existing_or_conflict(connection, existing, request_hash)
            raise BatchIdempotencyConflictError("batch_idempotency_unique_violation") from exc

    def acquire_dispatch_items(
        self,
        *,
        batch_id: str,
        worker_id: str,
        lease_seconds: int,
        limit: int,
        now: Any | None = None,
    ) -> list[ReportBatchItemRecord]:
        if limit < 1:
            return []

        lease_start = now or utc_now()
        lease_expiry = lease_start + timedelta(seconds=lease_seconds)
        lease_token = f"lease_{uuid4().hex}"
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM report_batch_item
                WHERE batch_id = %s
                  AND report_job_id IS NULL
                  AND (
                    status = 'materialized'
                    OR (
                      status = 'leased'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < %s
                    )
                  )
                ORDER BY item_position
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (batch_id, lease_start, limit),
            ).fetchall()
            if not rows:
                return []

            item_ids = [str(row["batch_item_id"]) for row in rows]
            connection.execute(
                """
                UPDATE report_batch_item
                SET status = 'leased',
                    lease_owner = %s,
                    lease_token = %s,
                    lease_acquired_at = %s,
                    lease_expires_at = %s,
                    last_heartbeat_at = %s
                WHERE batch_item_id = ANY(%s)
                """,
                (worker_id, lease_token, lease_start, lease_expiry, lease_start, item_ids),
            )
            connection.execute(
                """
                UPDATE report_batch
                SET status = 'running'
                WHERE batch_id = %s
                  AND status = 'materialized'
                """,
                (batch_id,),
            )
            refreshed_rows = connection.execute(
                """
                SELECT *
                FROM report_batch_item
                WHERE batch_item_id = ANY(%s)
                ORDER BY item_position
                """,
                (item_ids,),
            ).fetchall()
        return [_item_from_row(row) for row in refreshed_rows]

    def heartbeat_item_lease(
        self,
        *,
        batch_item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: Any | None = None,
    ) -> ReportBatchItemRecord:
        heartbeat_at = now or utc_now()
        expires_at = heartbeat_at + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE report_batch_item
                SET last_heartbeat_at = %s,
                    lease_expires_at = %s
                WHERE batch_item_id = %s
                  AND lease_token = %s
                  AND status = 'leased'
                RETURNING *
                """,
                (heartbeat_at, expires_at, batch_item_id, lease_token),
            ).fetchone()
            if not row:
                raise ValueError("report_batch_item_not_found")
        return _item_from_row(row)

    def mark_item_waiting_on_report_job(
        self,
        *,
        batch_item_id: str,
        lease_token: str,
        report_job_id: str,
        now: Any | None = None,
    ) -> ReportBatchItemRecord:
        dispatched_at = now or utc_now()
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE report_batch_item
                SET status = 'waiting_on_report_job',
                    report_job_id = %s,
                    dispatched_at = %s
                WHERE batch_item_id = %s
                  AND lease_token = %s
                  AND status = 'leased'
                RETURNING *
                """,
                (report_job_id, dispatched_at, batch_item_id, lease_token),
            ).fetchone()
            if not row:
                raise ValueError("report_batch_item_not_found")
        return _item_from_row(row)

    def count_active_batches(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM report_batch WHERE status = 'running'"
            ).fetchone()
        return int(row["count"])

    def count_active_items(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM report_batch_item
                WHERE status IN ('leased', 'waiting_on_report_job')
                """
            ).fetchone()
        return int(row["count"])

    def get_batch(self, batch_id: str) -> ReportBatchRecord:
        with self._connect() as connection:
            return self._load_batch(connection, batch_id)

    def _existing_or_conflict(
        self,
        connection: Connection[Mapping[str, Any]],
        existing: Mapping[str, Any],
        request_hash: str,
    ) -> ReportBatchRecord:
        if str(existing["request_hash"]) != request_hash:
            raise BatchIdempotencyConflictError(
                "batch_idempotency_key_reused_with_different_request"
            )
        return self._load_batch(connection, str(existing["batch_id"]))

    def _insert_items(
        self,
        *,
        connection: Connection[Mapping[str, Any]],
        batch_id: str,
        idempotency_key: str,
        request: BatchCreateRequest,
        materialized: list[MaterializedPortfolio],
        created_at: Any,
    ) -> None:
        for position, portfolio in enumerate(materialized, start=1):
            item_idempotency_key = (
                f"{idempotency_key}:{request.as_of_date.isoformat()}:"
                f"{position}:{portfolio.portfolio_id}"
            )
            connection.execute(
                """
                INSERT INTO report_batch_item (
                    batch_item_id, batch_id, item_position, portfolio_id,
                    item_idempotency_key, status, source_system, source_object,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    f"rbit_{uuid4().hex}",
                    batch_id,
                    position,
                    portfolio.portfolio_id,
                    item_idempotency_key,
                    "materialized",
                    portfolio.source_system,
                    portfolio.source_object,
                    created_at,
                ),
            )

    def _load_batch(
        self,
        connection: Connection[Mapping[str, Any]],
        batch_id: str,
    ) -> ReportBatchRecord:
        batch_row = connection.execute(
            "SELECT * FROM report_batch WHERE batch_id = %s",
            (batch_id,),
        ).fetchone()
        if not batch_row:
            raise ValueError("report_batch_not_found")
        item_rows = connection.execute(
            """
            SELECT *
            FROM report_batch_item
            WHERE batch_id = %s
            ORDER BY item_position
            """,
            (batch_id,),
        ).fetchall()
        return _batch_from_rows(batch_row, item_rows)


def _batch_from_rows(
    batch_row: Mapping[str, Any],
    item_rows: list[Mapping[str, Any]],
) -> ReportBatchRecord:
    return ReportBatchRecord(
        batch_id=str(batch_row["batch_id"]),
        selector_mode=batch_row["selector_mode"],
        tenant_id=str(batch_row["tenant_id"]),
        region=str(batch_row["region"]),
        materialized_portfolio_ids=list(batch_row["materialized_portfolio_ids_json"]),
        as_of_date=batch_row["as_of_date"],
        requested_output_formats=list(batch_row["requested_output_formats_json"]),
        reporting_currency=batch_row["reporting_currency"],
        options=dict(batch_row["options_json"]),
        idempotency_key=str(batch_row["idempotency_key"]),
        request_hash=str(batch_row["request_hash"]),
        status=batch_row["status"],
        item_count=int(batch_row["item_count"]),
        created_at=batch_row["created_at"],
        correlation_id=str(batch_row["correlation_id"]),
        trace_id=str(batch_row["trace_id"]),
        items=[_item_from_row(row) for row in item_rows],
    )


def _item_from_row(row: Mapping[str, Any]) -> ReportBatchItemRecord:
    return ReportBatchItemRecord(
        batch_item_id=str(row["batch_item_id"]),
        batch_id=str(row["batch_id"]),
        item_position=int(row["item_position"]),
        portfolio_id=str(row["portfolio_id"]),
        item_idempotency_key=str(row["item_idempotency_key"]),
        status=row["status"],
        source_system=str(row["source_system"]),
        source_object=str(row["source_object"]),
        created_at=row["created_at"],
        report_job_id=_nullable_str(row.get("report_job_id")),
        lease_owner=_nullable_str(row.get("lease_owner")),
        lease_token=_nullable_str(row.get("lease_token")),
        lease_acquired_at=row.get("lease_acquired_at"),
        lease_expires_at=row.get("lease_expires_at"),
        last_heartbeat_at=row.get("last_heartbeat_at"),
        dispatched_at=row.get("dispatched_at"),
    )


def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
