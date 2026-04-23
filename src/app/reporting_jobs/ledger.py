from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobLedgerRecord,
    ReportJobListFilters,
    ReportJobStatus,
    ReportStatusEvent,
)


class MissingIdempotencyKeyError(ValueError):
    pass


class IdempotencyConflictError(ValueError):
    pass


class ReportJobNotFoundError(ValueError):
    pass


class InvalidReportJobTransitionError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def compute_request_hash(
    *,
    report_type: str,
    request: PortfolioReviewJobRequest,
    caller_context: ReportCallerContext,
) -> str:
    hash_payload = {
        "report_type": report_type,
        "portfolio_scope": request.portfolio_scope,
        "as_of_date": request.as_of_date.isoformat(),
        "requested_output_formats": sorted(request.requested_output_formats),
        "reporting_currency": request.reporting_currency,
        "options": request.options,
        "tenant_id": caller_context.tenant_id,
        "region": caller_context.region,
    }
    return hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()


class ReportJobLedger:
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
                CREATE TABLE IF NOT EXISTS report_request (
                    report_request_id TEXT PRIMARY KEY,
                    report_type TEXT NOT NULL,
                    portfolio_scope_json TEXT NOT NULL,
                    requested_output_formats_json TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    reporting_currency TEXT,
                    options_json TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    triggered_by TEXT NOT NULL,
                    caller_application TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    booking_center_code TEXT,
                    role TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_job (
                    report_job_id TEXT PRIMARY KEY,
                    report_request_id TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    portfolio_scope_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    failure_category TEXT,
                    failure_message TEXT,
                    current_step TEXT NOT NULL,
                    retry_eligible INTEGER NOT NULL,
                    cancel_requested INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    cancelled_at TEXT,
                    FOREIGN KEY(report_request_id) REFERENCES report_request(report_request_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_status_event (
                    status_event_id TEXT PRIMARY KEY,
                    report_job_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    FOREIGN KEY(report_job_id) REFERENCES report_job(report_job_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_status_event_job_created
                ON report_status_event(report_job_id, created_at)
                """
            )

    def create_portfolio_review_job(
        self,
        *,
        request: PortfolioReviewJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportJobLedgerRecord:
        if not idempotency_key or not idempotency_key.strip():
            raise MissingIdempotencyKeyError("missing_idempotency_key")

        normalized_key = idempotency_key.strip()
        request_hash = compute_request_hash(
            report_type="portfolio_review",
            request=request,
            caller_context=caller_context,
        )

        with self._lock:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT report_request_id, request_hash
                    FROM report_request
                    WHERE idempotency_key = ?
                    """,
                    (normalized_key,),
                ).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflictError(
                            "idempotency_key_reused_with_different_request"
                        )
                    return self._load_by_request_id(connection, existing["report_request_id"])

                now = utc_now()
                request_id = f"rrq_{uuid4().hex}"
                job_id = f"rjob_{uuid4().hex}"
                now_text = _dt_to_text(now)
                portfolio_scope_json = canonical_json(request.portfolio_scope)
                output_formats_json = canonical_json(sorted(request.requested_output_formats))
                options_json = canonical_json(request.options)

                connection.execute(
                    """
                    INSERT INTO report_request (
                        report_request_id, report_type, portfolio_scope_json,
                        requested_output_formats_json, as_of_date, reporting_currency,
                        options_json, trigger_type, triggered_by, caller_application,
                        tenant_id, region, booking_center_code, role, idempotency_key,
                        request_hash, correlation_id, trace_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        "portfolio_review",
                        portfolio_scope_json,
                        output_formats_json,
                        request.as_of_date.isoformat(),
                        request.reporting_currency,
                        options_json,
                        caller_context.trigger_type,
                        caller_context.triggered_by,
                        caller_context.caller_application,
                        caller_context.tenant_id,
                        caller_context.region,
                        caller_context.booking_center_code,
                        caller_context.role,
                        normalized_key,
                        request_hash,
                        caller_context.correlation_id,
                        caller_context.trace_id,
                        now_text,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO report_job (
                        report_job_id, report_request_id, report_type, portfolio_scope_json,
                        status, failure_category, failure_message, current_step, retry_eligible,
                        cancel_requested, created_at, updated_at, started_at, completed_at,
                        cancelled_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        request_id,
                        "portfolio_review",
                        portfolio_scope_json,
                        "accepted",
                        None,
                        None,
                        "accepted",
                        0,
                        0,
                        now_text,
                        now_text,
                        None,
                        None,
                        None,
                    ),
                )
                self._append_status_event(
                    connection=connection,
                    job_id=job_id,
                    from_status=None,
                    to_status="accepted",
                    event_type="job_accepted",
                    message="Portfolio review report job accepted.",
                    actor=caller_context.triggered_by,
                    correlation_id=caller_context.correlation_id,
                    trace_id=caller_context.trace_id,
                    created_at=now,
                )
                return self._load_by_request_id(connection, request_id)

    def get_job(self, job_id: str) -> ReportJobLedgerRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_request_id FROM report_job WHERE report_job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                raise ReportJobNotFoundError("report_job_not_found")
            return self._load_by_request_id(connection, row["report_request_id"])

    def list_status_events(self, job_id: str) -> list[ReportStatusEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM report_status_event
                WHERE report_job_id = ?
                ORDER BY created_at ASC, status_event_id ASC
                """,
                (job_id,),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_jobs(self, *, filters: ReportJobListFilters) -> list[ReportJobLedgerRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    req.report_request_id,
                    req.report_type,
                    req.portfolio_scope_json AS request_portfolio_scope_json,
                    req.requested_output_formats_json,
                    req.as_of_date,
                    req.reporting_currency,
                    req.options_json,
                    req.trigger_type,
                    req.triggered_by,
                    req.caller_application,
                    req.tenant_id,
                    req.region,
                    req.booking_center_code,
                    req.role,
                    req.idempotency_key,
                    req.request_hash,
                    req.correlation_id,
                    req.trace_id,
                    req.created_at AS request_created_at,
                    job.report_job_id,
                    job.portfolio_scope_json AS job_portfolio_scope_json,
                    job.status,
                    job.failure_category,
                    job.failure_message,
                    job.current_step,
                    job.retry_eligible,
                    job.cancel_requested,
                    job.created_at AS job_created_at,
                    job.updated_at,
                    job.started_at,
                    job.completed_at,
                    job.cancelled_at
                FROM report_request req
                JOIN report_job job ON job.report_request_id = req.report_request_id
                ORDER BY job.created_at DESC, job.report_job_id DESC
                LIMIT ?
                """,
                (filters.limit,),
            ).fetchall()
        records = [_record_from_row(row) for row in rows]
        return [record for record in records if _record_matches_filters(record, filters)]

    def cancel_job(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord:
        with self._lock:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT status FROM report_job WHERE report_job_id = ?",
                    (job_id,),
                ).fetchone()
                if not existing:
                    raise ReportJobNotFoundError("report_job_not_found")
                current_status = existing["status"]
                if current_status in {"completed", "completed_with_warnings", "cancelled"}:
                    raise InvalidReportJobTransitionError("report_job_cannot_be_cancelled")

                now = utc_now()
                now_text = _dt_to_text(now)
                connection.execute(
                    """
                    UPDATE report_job
                    SET status = ?, failure_category = ?, failure_message = ?, current_step = ?,
                        retry_eligible = ?, cancel_requested = ?, updated_at = ?, cancelled_at = ?
                    WHERE report_job_id = ?
                    """,
                    (
                        "cancelled",
                        "cancelled",
                        "Report job cancelled before render or archive processing.",
                        "cancelled",
                        0,
                        1,
                        now_text,
                        now_text,
                        job_id,
                    ),
                )
                self._append_status_event(
                    connection=connection,
                    job_id=job_id,
                    from_status=current_status,
                    to_status="cancelled",
                    event_type="job_cancelled",
                    message="Report job cancelled before render or archive processing.",
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    created_at=now,
                )
                row = connection.execute(
                    "SELECT report_request_id FROM report_job WHERE report_job_id = ?",
                    (job_id,),
                ).fetchone()
                return self._load_by_request_id(connection, row["report_request_id"])

    def _append_status_event(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
        from_status: str | None,
        to_status: ReportJobStatus,
        event_type: str,
        message: str | None,
        actor: str,
        correlation_id: str,
        trace_id: str,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO report_status_event (
                status_event_id, report_job_id, from_status, to_status, event_type,
                message, actor, created_at, correlation_id, trace_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"rse_{uuid4().hex}",
                job_id,
                from_status,
                to_status,
                event_type,
                message,
                actor,
                _dt_to_text(created_at),
                correlation_id,
                trace_id,
            ),
        )

    def _load_by_request_id(
        self,
        connection: sqlite3.Connection,
        request_id: str,
    ) -> ReportJobLedgerRecord:
        row = connection.execute(
            """
            SELECT
                req.report_request_id,
                req.report_type,
                req.portfolio_scope_json AS request_portfolio_scope_json,
                req.requested_output_formats_json,
                req.as_of_date,
                req.reporting_currency,
                req.options_json,
                req.trigger_type,
                req.triggered_by,
                req.caller_application,
                req.tenant_id,
                req.region,
                req.booking_center_code,
                req.role,
                req.idempotency_key,
                req.request_hash,
                req.correlation_id,
                req.trace_id,
                req.created_at AS request_created_at,
                job.report_job_id,
                job.portfolio_scope_json AS job_portfolio_scope_json,
                job.status,
                job.failure_category,
                job.failure_message,
                job.current_step,
                job.retry_eligible,
                job.cancel_requested,
                job.created_at AS job_created_at,
                job.updated_at,
                job.started_at,
                job.completed_at,
                job.cancelled_at
            FROM report_request req
            JOIN report_job job ON job.report_request_id = req.report_request_id
            WHERE req.report_request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if not row:
            raise ReportJobNotFoundError("report_job_not_found")
        return _record_from_row(row)


def _dt_to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dt_from_text(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _record_from_row(row: sqlite3.Row) -> ReportJobLedgerRecord:
    return ReportJobLedgerRecord(
        request_id=row["report_request_id"],
        job_id=row["report_job_id"],
        report_type=row["report_type"],
        portfolio_scope=json.loads(row["request_portfolio_scope_json"]),
        requested_output_formats=json.loads(row["requested_output_formats_json"]),
        as_of_date=date.fromisoformat(row["as_of_date"]),
        reporting_currency=row["reporting_currency"],
        options=json.loads(row["options_json"]),
        trigger_type=row["trigger_type"],
        triggered_by=row["triggered_by"],
        caller_application=row["caller_application"],
        tenant_id=row["tenant_id"],
        region=row["region"],
        booking_center_code=row["booking_center_code"],
        role=row["role"],
        idempotency_key=row["idempotency_key"],
        request_hash=row["request_hash"],
        status=row["status"],
        failure_category=row["failure_category"],
        failure_message=row["failure_message"],
        current_step=row["current_step"],
        retry_eligible=bool(row["retry_eligible"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=_dt_from_text(row["job_created_at"]) or utc_now(),
        updated_at=_dt_from_text(row["updated_at"]) or utc_now(),
        started_at=_dt_from_text(row["started_at"]),
        completed_at=_dt_from_text(row["completed_at"]),
        cancelled_at=_dt_from_text(row["cancelled_at"]),
        correlation_id=row["correlation_id"],
        trace_id=row["trace_id"],
    )


def _event_from_row(row: sqlite3.Row) -> ReportStatusEvent:
    return ReportStatusEvent(
        status_event_id=row["status_event_id"],
        report_job_id=row["report_job_id"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        event_type=row["event_type"],
        message=row["message"],
        actor=row["actor"],
        created_at=_dt_from_text(row["created_at"]) or utc_now(),
        correlation_id=row["correlation_id"],
        trace_id=row["trace_id"],
    )


def _record_matches_filters(record: ReportJobLedgerRecord, filters: ReportJobListFilters) -> bool:
    if filters.tenant_id and record.tenant_id != filters.tenant_id:
        return False
    if filters.region and record.region != filters.region:
        return False
    if filters.status and record.status != filters.status:
        return False
    if filters.report_type and record.report_type != filters.report_type:
        return False
    if filters.portfolio_id and (
        filters.portfolio_id not in record.portfolio_scope.get("portfolio_ids", [])
    ):
        return False
    if filters.as_of_date and record.as_of_date != filters.as_of_date:
        return False
    if filters.idempotency_key and record.idempotency_key != filters.idempotency_key:
        return False
    if filters.correlation_id and record.correlation_id != filters.correlation_id:
        return False
    if filters.created_from and record.created_at < filters.created_from:
        return False
    if filters.created_to and record.created_at > filters.created_to:
        return False
    return True
