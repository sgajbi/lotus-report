from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
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
                    created_at TEXT NOT NULL
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
                    UNIQUE(batch_id, portfolio_id),
                    FOREIGN KEY(batch_id) REFERENCES report_batch(batch_id)
                )
                """
            )
            for column_name, column_type in (
                ("report_job_id", "TEXT"),
                ("lease_owner", "TEXT"),
                ("lease_token", "TEXT"),
                ("lease_acquired_at", "TEXT"),
                ("lease_expires_at", "TEXT"),
                ("last_heartbeat_at", "TEXT"),
                ("dispatched_at", "TEXT"),
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
                        correlation_id, trace_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                      AND (
                        status = 'materialized'
                        OR (
                          status = 'leased'
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at < ?
                        )
                      )
                    ORDER BY item_position
                    LIMIT ?
                    """,
                    (batch_id, _dt_to_text(lease_start), limit),
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
                        last_heartbeat_at = ?
                    WHERE batch_item_id IN ({placeholders})
                    """,
                    (
                        worker_id,
                        lease_token,
                        _dt_to_text(lease_start),
                        _dt_to_text(lease_expiry),
                        _dt_to_text(lease_start),
                        *item_ids,
                    ),
                )
                connection.execute(
                    """
                    UPDATE report_batch
                    SET status = 'running'
                    WHERE batch_id = ?
                      AND status = 'materialized'
                    """,
                    (batch_id,),
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
                if row is None:
                    raise ValueError("report_batch_item_not_found")
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
                if row is None:
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
