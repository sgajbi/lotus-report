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
    BatchControlResult,
    BatchCreateRequest,
    BatchRecoveryResult,
    BatchRetryPolicy,
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
                        correlation_id, trace_id, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                  AND EXISTS (
                    SELECT 1
                    FROM report_batch
                    WHERE report_batch.batch_id = report_batch_item.batch_id
                      AND report_batch.status IN ('materialized', 'running')
                  )
                  AND (
                    status = 'materialized'
                    OR status = 'recovery_pending'
                    OR (
                      status = 'leased'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < %s
                    )
                    OR (
                      status = 'failed_retryable'
                      AND retry_eligible IS TRUE
                      AND (next_retry_at IS NULL OR next_retry_at <= %s)
                    )
                  )
                ORDER BY item_position
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (batch_id, lease_start, lease_start, limit),
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
                    last_heartbeat_at = %s,
                    started_at = COALESCE(started_at, %s)
                WHERE batch_item_id = ANY(%s)
                """,
                (
                    worker_id,
                    lease_token,
                    lease_start,
                    lease_expiry,
                    lease_start,
                    lease_start,
                    item_ids,
                ),
            )
            connection.execute(
                """
                UPDATE report_batch
                SET status = 'running',
                    updated_at = %s,
                    started_at = COALESCE(started_at, %s)
                WHERE batch_id = %s
                  AND status = 'materialized'
                """,
                (lease_start, lease_start, batch_id),
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

    def mark_item_succeeded(
        self,
        *,
        batch_item_id: str,
        report_job_id: str,
        now: Any | None = None,
    ) -> ReportBatchItemRecord:
        completed_at = now or utc_now()
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE report_batch_item
                SET status = 'succeeded',
                    retry_eligible = FALSE,
                    next_retry_at = NULL,
                    last_error_category = NULL,
                    last_error_summary = NULL,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_acquired_at = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    completed_at = %s
                WHERE batch_item_id = %s
                  AND report_job_id = %s
                  AND status = 'waiting_on_report_job'
                RETURNING *
                """,
                (completed_at, batch_item_id, report_job_id),
            ).fetchone()
            if not row:
                raise ValueError("report_batch_item_not_found")
            self._refresh_batch_status(connection, str(row["batch_id"]), now=completed_at)
        return _item_from_row(row)

    def mark_item_failed(
        self,
        *,
        batch_item_id: str,
        error_category: str,
        error_summary: str,
        retryable: bool,
        retry_policy: BatchRetryPolicy | None = None,
        next_retry_at: Any | None = None,
        now: Any | None = None,
    ) -> ReportBatchItemRecord:
        failure_at = now or utc_now()
        policy = retry_policy or BatchRetryPolicy()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM report_batch_item
                WHERE batch_item_id = %s
                FOR UPDATE
                """,
                (batch_item_id,),
            ).fetchone()
            if not existing:
                raise ValueError("report_batch_item_not_found")
            attempt_count = int(existing["attempt_count"]) + 1
            status = (
                "failed_retryable"
                if retryable and attempt_count < policy.max_attempts
                else "failed_terminal"
            )
            retry_eligible = status == "failed_retryable"
            row = connection.execute(
                """
                UPDATE report_batch_item
                SET status = %s,
                    attempt_count = %s,
                    retry_eligible = %s,
                    next_retry_at = %s,
                    last_error_category = %s,
                    last_error_summary = %s,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_acquired_at = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    completed_at = %s
                WHERE batch_item_id = %s
                RETURNING *
                """,
                (
                    status,
                    attempt_count,
                    retry_eligible,
                    next_retry_at,
                    error_category,
                    error_summary,
                    failure_at,
                    batch_item_id,
                ),
            ).fetchone()
            if not row:
                raise ValueError("report_batch_item_not_found")
            self._refresh_batch_status(connection, str(row["batch_id"]), now=failure_at)
        return _item_from_row(row)

    def retry_failed_items(
        self,
        *,
        batch_id: str,
        retry_policy: BatchRetryPolicy | None = None,
        now: Any | None = None,
    ) -> BatchControlResult:
        retry_at = now or utc_now()
        policy = retry_policy or BatchRetryPolicy()
        with self._connect() as connection:
            rows = connection.execute(
                """
                UPDATE report_batch_item
                SET status = 'materialized',
                    retry_eligible = FALSE,
                    next_retry_at = NULL,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_acquired_at = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL
                WHERE batch_id = %s
                  AND status = 'failed_retryable'
                  AND report_job_id IS NULL
                  AND retry_eligible IS TRUE
                  AND attempt_count < %s
                  AND (next_retry_at IS NULL OR next_retry_at <= %s)
                RETURNING batch_item_id
                """,
                (batch_id, policy.max_attempts, retry_at),
            ).fetchall()
            self._refresh_batch_status(connection, batch_id, now=retry_at)
            batch = self._load_batch(connection, batch_id)
        return BatchControlResult(
            batch_id=batch_id,
            affected_count=len(rows),
            batch_status=batch.status,
        )

    def pause_batch(self, *, batch_id: str, now: Any | None = None) -> BatchControlResult:
        paused_at = now or utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                UPDATE report_batch
                SET status = 'paused',
                    updated_at = %s
                WHERE batch_id = %s
                  AND status IN ('materialized', 'running')
                RETURNING batch_id
                """,
                (paused_at, batch_id),
            ).fetchall()
            batch = self._load_batch(connection, batch_id)
        return BatchControlResult(
            batch_id=batch_id,
            affected_count=len(rows),
            batch_status=batch.status,
        )

    def resume_batch(self, *, batch_id: str, now: Any | None = None) -> BatchControlResult:
        resumed_at = now or utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                UPDATE report_batch
                SET status = 'materialized',
                    updated_at = %s
                WHERE batch_id = %s
                  AND status = 'paused'
                RETURNING batch_id
                """,
                (resumed_at, batch_id),
            ).fetchall()
            batch = self._load_batch(connection, batch_id)
        return BatchControlResult(
            batch_id=batch_id,
            affected_count=len(rows),
            batch_status=batch.status,
        )

    def cancel_batch(self, *, batch_id: str, now: Any | None = None) -> BatchControlResult:
        cancelled_at = now or utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                UPDATE report_batch_item
                SET status = 'cancelled',
                    retry_eligible = FALSE,
                    next_retry_at = NULL,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_acquired_at = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    cancelled_at = %s
                WHERE batch_id = %s
                  AND status IN (
                    'materialized',
                    'recovery_pending',
                    'failed_retryable',
                    'leased'
                  )
                  AND report_job_id IS NULL
                RETURNING batch_item_id
                """,
                (cancelled_at, batch_id),
            ).fetchall()
            connection.execute(
                """
                UPDATE report_batch
                SET status = 'cancelled',
                    updated_at = %s,
                    cancelled_at = %s
                WHERE batch_id = %s
                  AND status NOT IN ('completed', 'completed_with_failures', 'failed')
                """,
                (cancelled_at, cancelled_at, batch_id),
            )
            batch = self._load_batch(connection, batch_id)
        return BatchControlResult(
            batch_id=batch_id,
            affected_count=len(rows),
            batch_status=batch.status,
        )

    def recover_expired_leases(
        self,
        *,
        batch_id: str,
        now: Any | None = None,
    ) -> BatchRecoveryResult:
        recovery_at = now or utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                UPDATE report_batch_item
                SET status = 'recovery_pending',
                    retry_eligible = TRUE,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_acquired_at = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    last_error_category = 'expired_item_lease',
                    last_error_summary = 'Batch item lease expired before report-job dispatch.'
                WHERE batch_id = %s
                  AND status = 'leased'
                  AND report_job_id IS NULL
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < %s
                RETURNING batch_item_id
                """,
                (batch_id, recovery_at),
            ).fetchall()
            self._refresh_batch_status(connection, batch_id, now=recovery_at)
        return BatchRecoveryResult(
            batch_id=batch_id,
            recovered_count=len(rows),
            recovery_pending_item_ids=[str(row["batch_item_id"]) for row in rows],
        )

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

    def list_runnable_batch_ids(
        self,
        *,
        limit: int = 10,
        now: Any | None = None,
    ) -> list[str]:
        if limit < 1:
            return []

        scan_at = now or utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT report_batch.batch_id, report_batch.created_at
                FROM report_batch
                JOIN report_batch_item
                  ON report_batch_item.batch_id = report_batch.batch_id
                WHERE report_batch.status IN ('materialized', 'running')
                  AND (
                    report_batch_item.status IN (
                      'materialized',
                      'recovery_pending',
                      'waiting_on_report_job'
                    )
                    OR (
                      report_batch_item.status = 'leased'
                      AND report_batch_item.report_job_id IS NULL
                      AND report_batch_item.lease_expires_at IS NOT NULL
                      AND report_batch_item.lease_expires_at < %s
                    )
                    OR (
                      report_batch_item.status = 'failed_retryable'
                      AND report_batch_item.retry_eligible IS TRUE
                      AND (
                        report_batch_item.next_retry_at IS NULL
                        OR report_batch_item.next_retry_at <= %s
                      )
                    )
                  )
                ORDER BY report_batch.created_at, report_batch.batch_id
                LIMIT %s
                """,
                (scan_at, scan_at, limit),
            ).fetchall()
        return [str(row["batch_id"]) for row in rows]

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

    def _refresh_batch_status(
        self,
        connection: Connection[Mapping[str, Any]],
        batch_id: str,
        *,
        now: Any,
    ) -> None:
        rows = connection.execute(
            "SELECT status FROM report_batch_item WHERE batch_id = %s",
            (batch_id,),
        ).fetchall()
        if not rows:
            return
        statuses = {str(row["status"]) for row in rows}
        if statuses <= {"succeeded", "cancelled"}:
            status = "completed" if "cancelled" not in statuses else "cancelled"
        elif statuses <= {"succeeded", "failed_terminal", "cancelled"}:
            status = "completed_with_failures"
        elif "failed_retryable" in statuses and statuses <= {"succeeded", "failed_retryable"}:
            status = "failed"
        else:
            return
        connection.execute(
            """
            UPDATE report_batch
            SET status = %s,
                updated_at = %s,
                completed_at = CASE
                    WHEN %s IN ('completed', 'completed_with_failures')
                        THEN COALESCE(completed_at, %s)
                    ELSE completed_at
                END,
                cancelled_at = CASE
                    WHEN %s = 'cancelled' THEN COALESCE(cancelled_at, %s)
                    ELSE cancelled_at
                END,
                failed_at = CASE
                    WHEN %s = 'failed' THEN COALESCE(failed_at, %s)
                    ELSE failed_at
                END
            WHERE batch_id = %s
              AND status NOT IN ('paused', 'cancelled')
            """,
            (status, now, status, now, status, now, status, now, batch_id),
        )


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
        updated_at=batch_row.get("updated_at"),
        started_at=batch_row.get("started_at"),
        completed_at=batch_row.get("completed_at"),
        cancelled_at=batch_row.get("cancelled_at"),
        failed_at=batch_row.get("failed_at"),
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
        attempt_count=int(row.get("attempt_count") or 0),
        retry_eligible=bool(row.get("retry_eligible") or False),
        next_retry_at=row.get("next_retry_at"),
        last_error_category=_nullable_str(row.get("last_error_category")),
        last_error_summary=_nullable_str(row.get("last_error_summary")),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        cancelled_at=row.get("cancelled_at"),
    )


def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
