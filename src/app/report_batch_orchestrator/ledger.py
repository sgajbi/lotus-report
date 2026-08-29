from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator
from uuid import uuid4

if TYPE_CHECKING:
    from app.report_batch_orchestrator.schedule_definitions import (
        BatchScheduleAuditRecord,
        StoredBatchSchedule,
    )

from app.report_batch_orchestrator.lifecycle_policy import (
    batch_item_failure_outcome,
    reconciled_batch_status,
)
from app.report_batch_orchestrator.models import (
    BatchControlResult,
    BatchCreateRequest,
    BatchPressureSnapshot,
    BatchRecoveryResult,
    BatchRetryPolicy,
    MaterializedPortfolio,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.selector import materialize_portfolios
from app.reporting_jobs.ledger import canonical_json
from app.reporting_jobs.models import ReportCallerContext


class MissingBatchIdempotencyKeyError(ValueError):
    pass


class BatchIdempotencyConflictError(ValueError):
    pass


def compute_batch_request_hash(
    *,
    request: BatchCreateRequest,
    caller_context: ReportCallerContext,
    materialized_portfolios: list[MaterializedPortfolio],
) -> str:
    hash_payload = {
        "selector_mode": request.selector_mode,
        "materialized_portfolio_ids": [
            portfolio.portfolio_id for portfolio in materialized_portfolios
        ],
        "as_of_date": request.as_of_date.isoformat(),
        "requested_output_formats": sorted(request.requested_output_formats),
        "reporting_currency": request.reporting_currency,
        "options": request.options,
        "tenant_id": caller_context.tenant_id,
        "region": caller_context.region,
    }
    return hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReportBatchLedger:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self.ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._db_path != Path(":memory:"):
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
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
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_batch (
                    batch_id TEXT PRIMARY KEY,
                    selector_mode TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    materialized_portfolio_ids_json TEXT NOT NULL,
                    requested_output_formats_json TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    reporting_currency TEXT,
                    options_json TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    triggered_by TEXT NOT NULL,
                    caller_application TEXT NOT NULL,
                    booking_center_code TEXT,
                    role TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    correlation_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    cancelled_at TEXT,
                    failed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_batch_item (
                    batch_item_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    item_position INTEGER NOT NULL,
                    portfolio_id TEXT NOT NULL,
                    item_idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    source_object TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    report_job_id TEXT,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_acquired_at TEXT,
                    lease_expires_at TEXT,
                    last_heartbeat_at TEXT,
                    dispatched_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    retry_eligible INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    last_error_category TEXT,
                    last_error_summary TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    cancelled_at TEXT,
                    UNIQUE(batch_id, portfolio_id),
                    FOREIGN KEY(batch_id) REFERENCES report_batch(batch_id)
                )
                """
            )
            for column_name, column_type in (
                ("updated_at", "TEXT"),
                ("started_at", "TEXT"),
                ("completed_at", "TEXT"),
                ("cancelled_at", "TEXT"),
                ("failed_at", "TEXT"),
            ):
                _add_sqlite_column_if_missing(
                    connection,
                    table_name="report_batch",
                    column_name=column_name,
                    column_type=column_type,
                )
            for column_name, column_type in (
                ("report_job_id", "TEXT"),
                ("lease_owner", "TEXT"),
                ("lease_token", "TEXT"),
                ("lease_acquired_at", "TEXT"),
                ("lease_expires_at", "TEXT"),
                ("last_heartbeat_at", "TEXT"),
                ("dispatched_at", "TEXT"),
                ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
                ("retry_eligible", "INTEGER NOT NULL DEFAULT 0"),
                ("next_retry_at", "TEXT"),
                ("last_error_category", "TEXT"),
                ("last_error_summary", "TEXT"),
                ("started_at", "TEXT"),
                ("completed_at", "TEXT"),
                ("cancelled_at", "TEXT"),
            ):
                _add_sqlite_column_if_missing(
                    connection,
                    table_name="report_batch_item",
                    column_name=column_name,
                    column_type=column_type,
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_batch_tenant_region_created
                ON report_batch(tenant_id, region, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_batch_item_batch_position
                ON report_batch_item(batch_id, item_position)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_batch_item_portfolio
                ON report_batch_item(portfolio_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_batch_item_lease_expiry
                ON report_batch_item(status, lease_expires_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_batch_item_report_job
                ON report_batch_item(report_job_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_batch_item_retry
                ON report_batch_item(batch_id, status, next_retry_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_batch_schedule_definition (
                    schedule_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    booking_center_code TEXT,
                    owner_actor TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    cadence TEXT NOT NULL,
                    portfolio_ids_json TEXT NOT NULL,
                    requested_output_formats_json TEXT NOT NULL,
                    reporting_currency TEXT,
                    options_json TEXT NOT NULL,
                    max_batch_size INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_batch_schedule_tenant
                ON report_batch_schedule_definition(tenant_id, enabled)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_report_batch_schedule_fp_enabled
                ON report_batch_schedule_definition(fingerprint)
                WHERE enabled = 1
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_batch_schedule_audit (
                    audit_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    changes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_batch_schedule_audit_schedule
                ON report_batch_schedule_audit(schedule_id, created_at)
                """
            )

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

        with self._lock:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT batch_id, request_hash
                    FROM report_batch
                    WHERE idempotency_key = ?
                    """,
                    (normalized_key,),
                ).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise BatchIdempotencyConflictError(
                            "batch_idempotency_key_reused_with_different_request"
                        )
                    return self._load_batch(connection, str(existing["batch_id"]))

                now = utc_now()
                now_text = _dt_to_text(now)
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
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        request.selector_mode,
                        caller_context.tenant_id,
                        caller_context.region,
                        canonical_json(materialized_ids),
                        canonical_json(sorted(request.requested_output_formats)),
                        request.as_of_date.isoformat(),
                        request.reporting_currency,
                        canonical_json(request.options),
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
                        now_text,
                        now_text,
                    ),
                )
                for position, portfolio in enumerate(materialized, start=1):
                    item_idempotency_key = (
                        f"{normalized_key}:{request.as_of_date.isoformat()}:"
                        f"{position}:{portfolio.portfolio_id}"
                    )
                    connection.execute(
                        """
                        INSERT INTO report_batch_item (
                            batch_item_id, batch_id, item_position, portfolio_id,
                            item_idempotency_key, status, source_system, source_object,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            now_text,
                        ),
                    )
                return self._load_batch(connection, batch_id)

    def acquire_dispatch_items(
        self,
        *,
        batch_id: str,
        worker_id: str,
        lease_seconds: int,
        limit: int,
        now: datetime | None = None,
    ) -> list[ReportBatchItemRecord]:
        if limit < 1:
            return []

        lease_start = now or utc_now()
        lease_expiry = lease_start + timedelta(seconds=lease_seconds)
        lease_token = f"lease_{uuid4().hex}"
        with self._lock:
            with self._connect() as connection:
                item_rows = connection.execute(
                    """
                    SELECT *
                    FROM report_batch_item
                    WHERE batch_id = ?
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
                          AND lease_expires_at < ?
                        )
                        OR (
                          status = 'failed_retryable'
                          AND retry_eligible = 1
                          AND (next_retry_at IS NULL OR next_retry_at <= ?)
                        )
                      )
                    ORDER BY item_position
                    LIMIT ?
                    """,
                    (
                        batch_id,
                        _dt_to_text(lease_start),
                        _dt_to_text(lease_start),
                        limit,
                    ),
                ).fetchall()
                if not item_rows:
                    return []

                item_ids = [str(row["batch_item_id"]) for row in item_rows]
                placeholders = ",".join("?" for _ in item_ids)
                connection.execute(
                    f"""
                    UPDATE report_batch_item
                    SET status = 'leased',
                        lease_owner = ?,
                        lease_token = ?,
                        lease_acquired_at = ?,
                        lease_expires_at = ?,
                        last_heartbeat_at = ?,
                        started_at = COALESCE(started_at, ?)
                    WHERE batch_item_id IN ({placeholders})
                    """,
                    (
                        worker_id,
                        lease_token,
                        _dt_to_text(lease_start),
                        _dt_to_text(lease_expiry),
                        _dt_to_text(lease_start),
                        _dt_to_text(lease_start),
                        *item_ids,
                    ),
                )
                connection.execute(
                    """
                    UPDATE report_batch
                    SET status = 'running',
                        updated_at = ?,
                        started_at = COALESCE(started_at, ?)
                    WHERE batch_id = ?
                      AND status = 'materialized'
                    """,
                    (_dt_to_text(lease_start), _dt_to_text(lease_start), batch_id),
                )
                refreshed_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM report_batch_item
                    WHERE batch_item_id IN ({placeholders})
                    ORDER BY item_position
                    """,
                    item_ids,
                ).fetchall()
                return [_item_from_row(row) for row in refreshed_rows]

    def heartbeat_item_lease(
        self,
        *,
        batch_item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> ReportBatchItemRecord:
        heartbeat_at = now or utc_now()
        expires_at = heartbeat_at + timedelta(seconds=lease_seconds)
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE report_batch_item
                    SET last_heartbeat_at = ?,
                        lease_expires_at = ?
                    WHERE batch_item_id = ?
                      AND lease_token = ?
                      AND status = 'leased'
                    """,
                    (
                        _dt_to_text(heartbeat_at),
                        _dt_to_text(expires_at),
                        batch_item_id,
                        lease_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("report_batch_item_not_found")
                row = connection.execute(
                    "SELECT * FROM report_batch_item WHERE batch_item_id = ?",
                    (batch_item_id,),
                ).fetchone()
                return _item_from_row(row)

    def mark_item_waiting_on_report_job(
        self,
        *,
        batch_item_id: str,
        lease_token: str,
        report_job_id: str,
        now: datetime | None = None,
    ) -> ReportBatchItemRecord:
        dispatched_at = now or utc_now()
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE report_batch_item
                    SET status = 'waiting_on_report_job',
                        report_job_id = ?,
                        dispatched_at = ?
                    WHERE batch_item_id = ?
                      AND lease_token = ?
                      AND status = 'leased'
                    """,
                    (
                        report_job_id,
                        _dt_to_text(dispatched_at),
                        batch_item_id,
                        lease_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("report_batch_item_not_found")
                row = connection.execute(
                    "SELECT * FROM report_batch_item WHERE batch_item_id = ?",
                    (batch_item_id,),
                ).fetchone()
                return _item_from_row(row)

    def mark_item_succeeded(
        self,
        *,
        batch_item_id: str,
        report_job_id: str,
        now: datetime | None = None,
    ) -> ReportBatchItemRecord:
        completed_at = now or utc_now()
        with self._lock:
            with self._connect() as connection:
                updated = connection.execute(
                    """
                    UPDATE report_batch_item
                    SET status = 'succeeded',
                        retry_eligible = 0,
                        next_retry_at = NULL,
                        last_error_category = NULL,
                        last_error_summary = NULL,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_acquired_at = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        completed_at = ?
                    WHERE batch_item_id = ?
                      AND report_job_id = ?
                      AND status = 'waiting_on_report_job'
                    RETURNING *
                    """,
                    (
                        _dt_to_text(completed_at),
                        batch_item_id,
                        report_job_id,
                    ),
                ).fetchone()
                if updated is None:
                    raise ValueError("report_batch_item_not_found")
                self._refresh_batch_status(connection, str(updated["batch_id"]), now=completed_at)
                return _item_from_row(updated)

    def mark_item_failed(
        self,
        *,
        batch_item_id: str,
        error_category: str,
        error_summary: str,
        retryable: bool,
        retry_policy: BatchRetryPolicy | None = None,
        next_retry_at: datetime | None = None,
        now: datetime | None = None,
    ) -> ReportBatchItemRecord:
        failure_at = now or utc_now()
        policy = retry_policy or BatchRetryPolicy()
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM report_batch_item WHERE batch_item_id = ?",
                    (batch_item_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("report_batch_item_not_found")
                attempt_count = int(row["attempt_count"]) + 1
                status, retry_eligible = batch_item_failure_outcome(
                    retryable=retryable,
                    attempt_count=attempt_count,
                    max_attempts=policy.max_attempts,
                )
                updated = connection.execute(
                    """
                    UPDATE report_batch_item
                    SET status = ?,
                        attempt_count = ?,
                        retry_eligible = ?,
                        next_retry_at = ?,
                        last_error_category = ?,
                        last_error_summary = ?,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_acquired_at = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        completed_at = ?
                    WHERE batch_item_id = ?
                    RETURNING *
                    """,
                    (
                        status,
                        attempt_count,
                        1 if retry_eligible else 0,
                        _dt_to_text(next_retry_at) if next_retry_at else None,
                        error_category,
                        error_summary,
                        _dt_to_text(failure_at),
                        batch_item_id,
                    ),
                ).fetchone()
                if updated is None:
                    raise ValueError("report_batch_item_not_found")
                self._refresh_batch_status(connection, str(updated["batch_id"]), now=failure_at)
                return _item_from_row(updated)

    def retry_failed_items(
        self,
        *,
        batch_id: str,
        retry_policy: BatchRetryPolicy | None = None,
        now: datetime | None = None,
    ) -> BatchControlResult:
        retry_at = now or utc_now()
        policy = retry_policy or BatchRetryPolicy()
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE report_batch_item
                    SET status = 'materialized',
                        retry_eligible = 0,
                        next_retry_at = NULL,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_acquired_at = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL
                    WHERE batch_id = ?
                      AND status = 'failed_retryable'
                      AND report_job_id IS NULL
                      AND retry_eligible = 1
                      AND attempt_count < ?
                      AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    """,
                    (batch_id, policy.max_attempts, _dt_to_text(retry_at)),
                )
                self._refresh_batch_status(connection, batch_id, now=retry_at)
                batch = self._load_batch(connection, batch_id)
                return BatchControlResult(
                    batch_id=batch_id,
                    affected_count=cursor.rowcount,
                    batch_status=batch.status,
                )

    def pause_batch(self, *, batch_id: str, now: datetime | None = None) -> BatchControlResult:
        paused_at = now or utc_now()
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE report_batch
                    SET status = 'paused',
                        updated_at = ?
                    WHERE batch_id = ?
                      AND status IN ('materialized', 'running')
                    """,
                    (_dt_to_text(paused_at), batch_id),
                )
                batch = self._load_batch(connection, batch_id)
                return BatchControlResult(
                    batch_id=batch_id,
                    affected_count=cursor.rowcount,
                    batch_status=batch.status,
                )

    def resume_batch(self, *, batch_id: str, now: datetime | None = None) -> BatchControlResult:
        resumed_at = now or utc_now()
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE report_batch
                    SET status = 'materialized',
                        updated_at = ?
                    WHERE batch_id = ?
                      AND status = 'paused'
                    """,
                    (_dt_to_text(resumed_at), batch_id),
                )
                batch = self._load_batch(connection, batch_id)
                return BatchControlResult(
                    batch_id=batch_id,
                    affected_count=cursor.rowcount,
                    batch_status=batch.status,
                )

    def cancel_batch(self, *, batch_id: str, now: datetime | None = None) -> BatchControlResult:
        cancelled_at = now or utc_now()
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE report_batch_item
                    SET status = 'cancelled',
                        retry_eligible = 0,
                        next_retry_at = NULL,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_acquired_at = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        cancelled_at = ?
                    WHERE batch_id = ?
                      AND status IN (
                        'materialized',
                        'recovery_pending',
                        'failed_retryable',
                        'leased'
                      )
                      AND report_job_id IS NULL
                    """,
                    (_dt_to_text(cancelled_at), batch_id),
                )
                connection.execute(
                    """
                    UPDATE report_batch
                    SET status = 'cancelled',
                        updated_at = ?,
                        cancelled_at = ?
                    WHERE batch_id = ?
                      AND status NOT IN ('completed', 'completed_with_failures', 'failed')
                    """,
                    (_dt_to_text(cancelled_at), _dt_to_text(cancelled_at), batch_id),
                )
                batch = self._load_batch(connection, batch_id)
                return BatchControlResult(
                    batch_id=batch_id,
                    affected_count=cursor.rowcount,
                    batch_status=batch.status,
                )

    def recover_expired_leases(
        self,
        *,
        batch_id: str,
        now: datetime | None = None,
    ) -> BatchRecoveryResult:
        recovery_at = now or utc_now()
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    UPDATE report_batch_item
                    SET status = 'recovery_pending',
                        retry_eligible = 1,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_acquired_at = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        last_error_category = 'expired_item_lease',
                        last_error_summary = 'Batch item lease expired before report-job dispatch.'
                    WHERE batch_id = ?
                      AND status = 'leased'
                      AND report_job_id IS NULL
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < ?
                    RETURNING *
                    """,
                    (batch_id, _dt_to_text(recovery_at)),
                ).fetchall()
                self._refresh_batch_status(connection, batch_id, now=recovery_at)
                return BatchRecoveryResult(
                    batch_id=batch_id,
                    recovered_count=len(rows),
                    recovery_pending_item_ids=[str(row["batch_item_id"]) for row in rows],
                )

    def relink_failed_item_for_replay(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
        replayed_report_job_id: str,
        retry_policy: BatchRetryPolicy | None = None,
        now: datetime | None = None,
    ) -> ReportBatchItemRecord:
        replay_at = now or utc_now()
        policy = retry_policy or BatchRetryPolicy()
        with self._lock:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT *
                    FROM report_batch_item
                    WHERE batch_id = ? AND batch_item_id = ?
                    """,
                    (batch_id, batch_item_id),
                ).fetchone()
                if existing is None:
                    self._load_batch(connection, batch_id)
                    raise ValueError("report_batch_item_not_found")
                if (
                    existing["status"] == "waiting_on_report_job"
                    and existing["report_job_id"] == replayed_report_job_id
                ):
                    return _item_from_row(existing)
                updated = connection.execute(
                    """
                    UPDATE report_batch_item
                    SET status = 'waiting_on_report_job',
                        report_job_id = ?,
                        retry_eligible = 0,
                        next_retry_at = NULL,
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_acquired_at = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat_at = NULL,
                        last_error_category = NULL,
                        last_error_summary = NULL,
                        started_at = COALESCE(started_at, ?),
                        completed_at = NULL,
                        cancelled_at = NULL
                    WHERE batch_id = ?
                      AND batch_item_id = ?
                      AND status = 'failed_retryable'
                      AND retry_eligible = 1
                      AND lease_token IS NULL
                      AND attempt_count < ?
                    RETURNING *
                    """,
                    (
                        replayed_report_job_id,
                        _dt_to_text(replay_at),
                        batch_id,
                        batch_item_id,
                        policy.max_attempts,
                    ),
                ).fetchone()
                if updated is None:
                    raise ValueError("report_batch_item_cannot_be_replayed")
                self._refresh_batch_status(connection, batch_id, now=replay_at)
                return _item_from_row(updated)

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

    def batch_pressure_snapshot(self, *, now: datetime | None = None) -> BatchPressureSnapshot:
        sample_at = now or utc_now()
        with self._connect() as connection:
            active_batches = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM report_batch WHERE status = 'running'"
                ).fetchone()["count"]
            )
            active_items = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM report_batch_item
                    WHERE status IN ('leased', 'waiting_on_report_job')
                    """
                ).fetchone()["count"]
            )
            dispatch_ready_items = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM report_batch_item
                    WHERE report_job_id IS NULL
                      AND (
                        status = 'materialized'
                        OR status = 'recovery_pending'
                        OR (
                          status = 'leased'
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at < ?
                        )
                        OR (
                          status = 'failed_retryable'
                          AND retry_eligible = 1
                          AND (next_retry_at IS NULL OR next_retry_at <= ?)
                        )
                      )
                    """,
                    (_dt_to_text(sample_at), _dt_to_text(sample_at)),
                ).fetchone()["count"]
            )
            retry_ready_items = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM report_batch_item
                    WHERE status = 'failed_retryable'
                      AND retry_eligible = 1
                      AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    """,
                    (_dt_to_text(sample_at),),
                ).fetchone()["count"]
            )
            recovery_pending_items = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM report_batch_item
                    WHERE status = 'recovery_pending'
                    """
                ).fetchone()["count"]
            )
            runnable_batches = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM (
                        SELECT DISTINCT report_batch.batch_id
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
                              AND report_batch_item.lease_expires_at < ?
                            )
                            OR (
                              report_batch_item.status = 'failed_retryable'
                              AND report_batch_item.retry_eligible = 1
                              AND (
                                report_batch_item.next_retry_at IS NULL
                                OR report_batch_item.next_retry_at <= ?
                              )
                            )
                          )
                    )
                    """,
                    (_dt_to_text(sample_at), _dt_to_text(sample_at)),
                ).fetchone()["count"]
            )
        return BatchPressureSnapshot(
            runnable_batches=runnable_batches,
            active_batches=active_batches,
            active_items=active_items,
            dispatch_ready_items=dispatch_ready_items,
            retry_ready_items=retry_ready_items,
            recovery_pending_items=recovery_pending_items,
        )

    def list_runnable_batch_ids(
        self,
        *,
        tenant_id: str,
        limit: int = 10,
        now: datetime | None = None,
    ) -> list[str]:
        """List runnable batches for one tenant only.

        tenant_id is required rather than optional so a background caller cannot
        accidentally scan every tenant by omitting it.
        """

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
                WHERE report_batch.tenant_id = ?
                  AND report_batch.status IN ('materialized', 'running')
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
                      AND report_batch_item.lease_expires_at < ?
                    )
                    OR (
                      report_batch_item.status = 'failed_retryable'
                      AND report_batch_item.retry_eligible = 1
                      AND (
                        report_batch_item.next_retry_at IS NULL
                        OR report_batch_item.next_retry_at <= ?
                      )
                    )
                  )
                ORDER BY report_batch.created_at, report_batch.batch_id
                LIMIT ?
                """,
                (tenant_id, _dt_to_text(scan_at), _dt_to_text(scan_at), limit),
            ).fetchall()
        return [str(row["batch_id"]) for row in rows]

    def list_attention_batch_ids(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
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
                      'leased',
                      'waiting_on_report_job',
                      'recovery_pending'
                    )
                    OR (
                      report_batch_item.status = 'failed_retryable'
                      AND report_batch_item.retry_eligible = 1
                      AND (
                        report_batch_item.next_retry_at IS NULL
                        OR report_batch_item.next_retry_at <= ?
                      )
                    )
                  )
                ORDER BY report_batch.created_at, report_batch.batch_id
                LIMIT ?
                """,
                (_dt_to_text(scan_at), limit),
            ).fetchall()
        return [str(row["batch_id"]) for row in rows]

    def save_schedule_definition(self, schedule: "StoredBatchSchedule") -> "StoredBatchSchedule":
        with self._lock, self._connect() as connection:
            self._write_schedule_definition(connection, schedule)
        return schedule

    def save_schedule_definition_with_audit(
        self,
        schedule: "StoredBatchSchedule",
        record: "BatchScheduleAuditRecord",
    ) -> "StoredBatchSchedule":
        """Definition and audit event in one transaction - a schedule must never
        exist without the audit record that explains it."""
        with self._lock, self._connect() as connection:
            self._write_schedule_definition(connection, schedule)
            self._write_schedule_audit(connection, record)
        return schedule

    def _write_schedule_definition(
        self, connection: sqlite3.Connection, schedule: "StoredBatchSchedule"
    ) -> None:
        from app.report_batch_orchestrator.schedule_definitions import (
            DuplicateScheduleDefinition,
            StaleScheduleRevision,
        )

        if schedule.revision == 1:
            try:
                connection.execute(
                    """
                    INSERT INTO report_batch_schedule_definition (
                        schedule_id, tenant_id, region, booking_center_code, owner_actor,
                        enabled, cadence, portfolio_ids_json,
                        requested_output_formats_json, reporting_currency, options_json,
                        max_batch_size, fingerprint, revision, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        schedule.schedule_id,
                        schedule.tenant_id,
                        schedule.region,
                        schedule.booking_center_code,
                        schedule.owner_actor,
                        1 if schedule.enabled else 0,
                        schedule.cadence,
                        json.dumps(schedule.portfolio_ids),
                        json.dumps(schedule.requested_output_formats),
                        schedule.reporting_currency,
                        json.dumps(schedule.options),
                        schedule.max_batch_size,
                        schedule.fingerprint,
                        schedule.revision,
                        schedule.created_at.isoformat(),
                        schedule.updated_at.isoformat() if schedule.updated_at else None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "fingerprint" not in str(exc):
                    raise
                row = connection.execute(
                    """
                    SELECT schedule_id FROM report_batch_schedule_definition
                    WHERE fingerprint = ? AND enabled = 1
                    """,
                    (schedule.fingerprint,),
                ).fetchone()
                raise DuplicateScheduleDefinition(
                    row["schedule_id"] if row else schedule.schedule_id
                ) from exc
            return
        cursor = connection.execute(
            """
            UPDATE report_batch_schedule_definition SET
                enabled = ?,
                cadence = ?,
                portfolio_ids_json = ?,
                requested_output_formats_json = ?,
                reporting_currency = ?,
                options_json = ?,
                max_batch_size = ?,
                fingerprint = ?,
                revision = ?,
                updated_at = ?
            WHERE schedule_id = ? AND revision = ?
            """,
            (
                1 if schedule.enabled else 0,
                schedule.cadence,
                json.dumps(schedule.portfolio_ids),
                json.dumps(schedule.requested_output_formats),
                schedule.reporting_currency,
                json.dumps(schedule.options),
                schedule.max_batch_size,
                schedule.fingerprint,
                schedule.revision,
                schedule.updated_at.isoformat() if schedule.updated_at else None,
                schedule.schedule_id,
                schedule.revision - 1,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleScheduleRevision(schedule.schedule_id)

    def get_schedule_definition(self, schedule_id: str) -> "StoredBatchSchedule | None":
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_batch_schedule_definition WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()
        if row is None:
            return None
        return _schedule_from_row(row)

    def list_schedule_definitions(self, tenant_id: str) -> "list[StoredBatchSchedule]":
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM report_batch_schedule_definition
                WHERE tenant_id = ?
                ORDER BY created_at, schedule_id
                """,
                (tenant_id,),
            ).fetchall()
        return [_schedule_from_row(row) for row in rows]

    def append_schedule_audit(
        self, record: "BatchScheduleAuditRecord"
    ) -> "BatchScheduleAuditRecord":
        with self._lock, self._connect() as connection:
            self._write_schedule_audit(connection, record)
        return record

    def _write_schedule_audit(
        self, connection: sqlite3.Connection, record: "BatchScheduleAuditRecord"
    ) -> None:
        connection.execute(
            """
                INSERT INTO report_batch_schedule_audit (
                    audit_id, schedule_id, action, actor, correlation_id,
                    changes_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
            (
                record.audit_id,
                record.schedule_id,
                record.action,
                record.actor,
                record.correlation_id,
                json.dumps(record.changes),
                record.created_at.isoformat(),
            ),
        )

    def list_schedule_audit(self, schedule_id: str) -> "list[BatchScheduleAuditRecord]":
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM report_batch_schedule_audit
                WHERE schedule_id = ?
                ORDER BY rowid
                """,
                (schedule_id,),
            ).fetchall()
        return [_audit_from_row(row) for row in rows]

    def get_batch(self, batch_id: str) -> ReportBatchRecord:
        with self._connect() as connection:
            return self._load_batch(connection, batch_id)

    def get_batch_item(self, batch_id: str, batch_item_id: str) -> ReportBatchItemRecord:
        with self._connect() as connection:
            self._load_batch(connection, batch_id)
            row = connection.execute(
                """
                SELECT *
                FROM report_batch_item
                WHERE batch_id = ? AND batch_item_id = ?
                """,
                (batch_id, batch_item_id),
            ).fetchone()
            if row is None:
                raise ValueError("report_batch_item_not_found")
            return _item_from_row(row)

    def _load_batch(self, connection: sqlite3.Connection, batch_id: str) -> ReportBatchRecord:
        batch_row = connection.execute(
            "SELECT * FROM report_batch WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if batch_row is None:
            raise ValueError("report_batch_not_found")
        item_rows = connection.execute(
            """
            SELECT *
            FROM report_batch_item
            WHERE batch_id = ?
            ORDER BY item_position
            """,
            (batch_id,),
        ).fetchall()
        return _batch_from_rows(batch_row, item_rows)

    def _refresh_batch_status(
        self,
        connection: sqlite3.Connection,
        batch_id: str,
        *,
        now: datetime,
    ) -> None:
        rows = connection.execute(
            "SELECT status FROM report_batch_item WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
        if not rows:
            return
        status = reconciled_batch_status(str(row["status"]) for row in rows)
        if status is None:
            return
        connection.execute(
            """
            UPDATE report_batch
            SET status = ?,
                updated_at = ?,
                completed_at = CASE
                    WHEN ? IN ('completed', 'completed_with_failures')
                        THEN COALESCE(completed_at, ?)
                    ELSE completed_at
                END,
                cancelled_at = CASE
                    WHEN ? = 'cancelled' THEN COALESCE(cancelled_at, ?)
                    ELSE cancelled_at
                END,
                failed_at = CASE
                    WHEN ? = 'failed' THEN COALESCE(failed_at, ?)
                    ELSE failed_at
                END
            WHERE batch_id = ?
              AND status NOT IN ('paused', 'cancelled')
            """,
            (
                status,
                _dt_to_text(now),
                status,
                _dt_to_text(now),
                status,
                _dt_to_text(now),
                status,
                _dt_to_text(now),
                batch_id,
            ),
        )


def _batch_from_rows(
    batch_row: sqlite3.Row,
    item_rows: list[sqlite3.Row],
) -> ReportBatchRecord:
    return ReportBatchRecord(
        batch_id=str(batch_row["batch_id"]),
        selector_mode=batch_row["selector_mode"],
        tenant_id=str(batch_row["tenant_id"]),
        region=str(batch_row["region"]),
        materialized_portfolio_ids=_json_list(batch_row["materialized_portfolio_ids_json"]),
        as_of_date=batch_row["as_of_date"],
        requested_output_formats=_json_list(batch_row["requested_output_formats_json"]),
        reporting_currency=batch_row["reporting_currency"],
        options=_json_dict(batch_row["options_json"]),
        idempotency_key=str(batch_row["idempotency_key"]),
        request_hash=str(batch_row["request_hash"]),
        status=batch_row["status"],
        item_count=int(batch_row["item_count"]),
        created_at=_dt_from_text(str(batch_row["created_at"])),
        updated_at=_nullable_dt(_optional_row_value(batch_row, "updated_at")),
        started_at=_nullable_dt(_optional_row_value(batch_row, "started_at")),
        completed_at=_nullable_dt(_optional_row_value(batch_row, "completed_at")),
        cancelled_at=_nullable_dt(_optional_row_value(batch_row, "cancelled_at")),
        failed_at=_nullable_dt(_optional_row_value(batch_row, "failed_at")),
        correlation_id=str(batch_row["correlation_id"]),
        trace_id=str(batch_row["trace_id"]),
        items=[_item_from_row(row) for row in item_rows],
    )


def _item_from_row(row: sqlite3.Row) -> ReportBatchItemRecord:
    return ReportBatchItemRecord(
        batch_item_id=str(row["batch_item_id"]),
        batch_id=str(row["batch_id"]),
        item_position=int(row["item_position"]),
        portfolio_id=str(row["portfolio_id"]),
        item_idempotency_key=str(row["item_idempotency_key"]),
        status=row["status"],
        source_system=str(row["source_system"]),
        source_object=str(row["source_object"]),
        created_at=_dt_from_text(str(row["created_at"])),
        report_job_id=_nullable_str(row["report_job_id"]),
        lease_owner=_nullable_str(row["lease_owner"]),
        lease_token=_nullable_str(row["lease_token"]),
        lease_acquired_at=_nullable_dt(row["lease_acquired_at"]),
        lease_expires_at=_nullable_dt(row["lease_expires_at"]),
        last_heartbeat_at=_nullable_dt(row["last_heartbeat_at"]),
        dispatched_at=_nullable_dt(row["dispatched_at"]),
        attempt_count=int(_optional_row_value(row, "attempt_count") or 0),
        retry_eligible=bool(_optional_row_value(row, "retry_eligible") or 0),
        next_retry_at=_nullable_dt(_optional_row_value(row, "next_retry_at")),
        last_error_category=_nullable_str(_optional_row_value(row, "last_error_category")),
        last_error_summary=_nullable_str(_optional_row_value(row, "last_error_summary")),
        started_at=_nullable_dt(_optional_row_value(row, "started_at")),
        completed_at=_nullable_dt(_optional_row_value(row, "completed_at")),
        cancelled_at=_nullable_dt(_optional_row_value(row, "cancelled_at")),
    )


def _json_list(value: Any) -> list[str]:
    import json

    return [str(item) for item in json.loads(str(value))]


def _json_dict(value: Any) -> dict[str, Any]:
    import json

    loaded = json.loads(str(value))
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def _dt_to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dt_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _nullable_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return _dt_from_text(str(value))


def _optional_row_value(row: sqlite3.Row, column_name: str) -> Any | None:
    if column_name not in row.keys():
        return None
    return row[column_name]


def _add_sqlite_column_if_missing(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    column_type: str,
) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _schedule_from_row(row: sqlite3.Row) -> "StoredBatchSchedule":
    from datetime import datetime as _datetime

    from app.report_batch_orchestrator.schedule_definitions import StoredBatchSchedule

    return StoredBatchSchedule(
        schedule_id=row["schedule_id"],
        tenant_id=row["tenant_id"],
        region=row["region"],
        booking_center_code=row["booking_center_code"],
        owner_actor=row["owner_actor"],
        enabled=bool(row["enabled"]),
        cadence=row["cadence"],
        portfolio_ids=_json_list(row["portfolio_ids_json"]),
        requested_output_formats=_json_list(row["requested_output_formats_json"]),
        reporting_currency=row["reporting_currency"],
        options=_json_dict(row["options_json"]),
        max_batch_size=row["max_batch_size"],
        revision=row["revision"],
        created_at=_datetime.fromisoformat(row["created_at"]),
        updated_at=(_datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None),
    )


def _audit_from_row(row: sqlite3.Row) -> "BatchScheduleAuditRecord":
    from datetime import datetime as _datetime

    from app.report_batch_orchestrator.schedule_definitions import BatchScheduleAuditRecord

    return BatchScheduleAuditRecord(
        audit_id=row["audit_id"],
        schedule_id=row["schedule_id"],
        action=row["action"],
        actor=row["actor"],
        correlation_id=row["correlation_id"],
        changes=_json_dict(row["changes_json"]),
        created_at=_datetime.fromisoformat(row["created_at"]),
    )
