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
    OutcomeReviewReportJobRequest,
    PortfolioReviewJobRequest,
    ProofPackReportJobRequest,
    ReportCallerContext,
    ReportJobLedgerRecord,
    ReportJobListFilters,
    ReportJobStatus,
    ReportRerenderAttemptRecord,
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
    request: PortfolioReviewJobRequest | OutcomeReviewReportJobRequest | ProofPackReportJobRequest,
    caller_context: ReportCallerContext,
) -> str:
    portfolio_scope, as_of_date, output_formats, reporting_currency, options = _request_parts(
        report_type=report_type,
        request=request,
    )
    hash_payload = {
        "report_type": report_type,
        "portfolio_scope": portfolio_scope,
        "as_of_date": as_of_date.isoformat(),
        "requested_output_formats": sorted(output_formats),
        "reporting_currency": reporting_currency,
        "options": options,
        "tenant_id": caller_context.tenant_id,
        "region": caller_context.region,
    }
    return hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()


def _request_parts(
    *,
    report_type: str,
    request: PortfolioReviewJobRequest | OutcomeReviewReportJobRequest | ProofPackReportJobRequest,
) -> tuple[dict[str, Any], date, list[str], str | None, dict[str, Any]]:
    if isinstance(request, PortfolioReviewJobRequest):
        return (
            request.portfolio_scope,
            request.as_of_date,
            request.requested_output_formats,
            request.reporting_currency,
            request.options,
        )
    if isinstance(request, ProofPackReportJobRequest):
        report_input = request.proof_pack_report_input
        portfolio_id = str(report_input.get("portfolio_id") or "").strip()
        if not portfolio_id:
            raise ValueError("proof_pack_report_input.portfolio_id is required")
        as_of_text = report_input.get("as_of_date") or report_input.get("generated_at")
        if not as_of_text:
            raise ValueError("proof_pack_report_input.as_of_date is required")
        as_of_date = date.fromisoformat(str(as_of_text)[:10])
        options = dict(request.options)
        options["proof_pack_report_input"] = report_input
        portfolio_scope = {
            "portfolio_ids": [portfolio_id],
            "proof_pack_id": report_input.get("proof_pack_id"),
        }
        return (
            portfolio_scope,
            as_of_date,
            request.requested_output_formats,
            request.reporting_currency,
            options,
        )
    report_input = request.outcome_report_input
    portfolio_id = str(report_input.get("portfolio_id") or "").strip()
    if not portfolio_id:
        raise ValueError("outcome_report_input.portfolio_id is required")
    review_window = report_input.get("review_window")
    review_window_payload = review_window if isinstance(review_window, dict) else {}
    as_of_text = (
        review_window_payload.get("end_date")
        or review_window_payload.get("period_end")
        or report_input.get("generated_at")
    )
    if not as_of_text:
        raise ValueError("outcome_report_input review window end date is required")
    as_of_date = date.fromisoformat(str(as_of_text)[:10])
    options = dict(request.options)
    options["outcome_report_input"] = report_input
    portfolio_scope = {
        "portfolio_ids": [portfolio_id],
        "outcome_review_id": report_input.get("outcome_review_id"),
    }
    return (
        portfolio_scope,
        as_of_date,
        request.requested_output_formats,
        request.reporting_currency,
        options,
    )


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
                    render_job_id TEXT,
                    render_output_format TEXT,
                    render_template_id TEXT,
                    render_template_version TEXT,
                    render_artifact_sha256 TEXT,
                    render_bounded_determinism_fingerprint TEXT,
                    render_runtime_engine TEXT,
                    render_runtime_engine_version TEXT,
                    render_duration_ms INTEGER,
                    archive_request_id TEXT,
                    archive_document_id TEXT,
                    archive_completed_at TEXT,
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS report_rerender_attempt (
                    rerender_attempt_id TEXT PRIMARY KEY,
                    report_job_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    previous_render_job_id TEXT,
                    previous_archive_document_id TEXT,
                    render_job_id TEXT NOT NULL,
                    render_output_format TEXT NOT NULL,
                    render_template_id TEXT NOT NULL,
                    render_template_version TEXT NOT NULL,
                    render_artifact_sha256 TEXT,
                    render_bounded_determinism_fingerprint TEXT,
                    render_runtime_engine TEXT,
                    render_runtime_engine_version TEXT,
                    render_duration_ms INTEGER,
                    archive_request_id TEXT,
                    archive_document_id TEXT,
                    archive_completed_at TEXT,
                    failure_category TEXT,
                    failure_message TEXT,
                    retry_eligible INTEGER NOT NULL,
                    requested_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(report_job_id, idempotency_key),
                    FOREIGN KEY(report_job_id) REFERENCES report_job(report_job_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_report_rerender_attempt_job_created
                ON report_rerender_attempt(report_job_id, created_at)
                """
            )

    def create_portfolio_review_job(
        self,
        *,
        request: PortfolioReviewJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportJobLedgerRecord:
        return self._create_report_job(
            report_type="portfolio_review",
            accepted_message="Portfolio review report job accepted.",
            request=request,
            caller_context=caller_context,
            idempotency_key=idempotency_key,
        )

    def create_outcome_review_report_job(
        self,
        *,
        request: OutcomeReviewReportJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportJobLedgerRecord:
        return self._create_report_job(
            report_type="outcome_review",
            accepted_message="Outcome review report job accepted.",
            request=request,
            caller_context=caller_context,
            idempotency_key=idempotency_key,
        )

    def create_proof_pack_report_job(
        self,
        *,
        request: ProofPackReportJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportJobLedgerRecord:
        return self._create_report_job(
            report_type="proof_pack",
            accepted_message="Proof-pack report job accepted.",
            request=request,
            caller_context=caller_context,
            idempotency_key=idempotency_key,
        )

    def _create_report_job(
        self,
        *,
        report_type: str,
        accepted_message: str,
        request: PortfolioReviewJobRequest
        | OutcomeReviewReportJobRequest
        | ProofPackReportJobRequest,
        caller_context: ReportCallerContext,
        idempotency_key: str | None,
    ) -> ReportJobLedgerRecord:
        if not idempotency_key or not idempotency_key.strip():
            raise MissingIdempotencyKeyError("missing_idempotency_key")

        portfolio_scope, as_of_date, output_formats, reporting_currency, options = _request_parts(
            report_type=report_type,
            request=request,
        )
        normalized_key = idempotency_key.strip()
        request_hash = compute_request_hash(
            report_type=report_type,
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
                portfolio_scope_json = canonical_json(portfolio_scope)
                output_formats_json = canonical_json(sorted(output_formats))
                options_json = canonical_json(options)

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
                        report_type,
                        portfolio_scope_json,
                        output_formats_json,
                        as_of_date.isoformat(),
                        reporting_currency,
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
                        report_type,
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
                    message=accepted_message,
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

    def append_job_event(
        self,
        *,
        job_id: str,
        event_type: str,
        message: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> None:
        with self._lock:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT status FROM report_job WHERE report_job_id = ?",
                    (job_id,),
                ).fetchone()
                if not existing:
                    raise ReportJobNotFoundError("report_job_not_found")
                current_status: ReportJobStatus = existing["status"]
                self._append_status_event(
                    connection=connection,
                    job_id=job_id,
                    from_status=current_status,
                    to_status=current_status,
                    event_type=event_type,
                    message=message,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    created_at=utc_now(),
                )

    def create_rerender_attempt(
        self,
        *,
        job: ReportJobLedgerRecord,
        snapshot_id: str,
        snapshot_hash: str,
        idempotency_key: str,
        actor: str,
        reason: str,
        correlation_id: str,
        trace_id: str,
    ) -> tuple[ReportRerenderAttemptRecord, bool]:
        if not idempotency_key or not idempotency_key.strip():
            raise MissingIdempotencyKeyError("missing_idempotency_key")
        normalized_key = idempotency_key.strip()
        with self._lock:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT *
                    FROM report_rerender_attempt
                    WHERE report_job_id = ? AND idempotency_key = ?
                    """,
                    (job.job_id, normalized_key),
                ).fetchone()
                if existing:
                    return _rerender_attempt_from_row(existing), False
                now = utc_now()
                now_text = _dt_to_text(now)
                attempt_id = f"rrnd_{uuid4().hex}"
                render_job_id = f"rdr_{attempt_id}_pdf"
                connection.execute(
                    """
                    INSERT INTO report_rerender_attempt (
                        rerender_attempt_id, report_job_id, idempotency_key, status,
                        snapshot_id, snapshot_hash, previous_render_job_id,
                        previous_archive_document_id, render_job_id, render_output_format,
                        render_template_id, render_template_version, retry_eligible,
                        requested_by, reason, correlation_id, trace_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        job.job_id,
                        normalized_key,
                        "rendering",
                        snapshot_id,
                        snapshot_hash,
                        job.render_job_id,
                        job.archive_document_id,
                        render_job_id,
                        "pdf",
                        "portfolio-review",
                        "v1",
                        0,
                        actor,
                        reason,
                        correlation_id,
                        trace_id,
                        now_text,
                        now_text,
                    ),
                )
                self._append_status_event(
                    connection=connection,
                    job_id=job.job_id,
                    from_status=job.status,
                    to_status=job.status,
                    event_type="job_rerender_requested",
                    message=f"Report rerender requested from snapshot {snapshot_id}.",
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    created_at=now,
                )
                row = connection.execute(
                    "SELECT * FROM report_rerender_attempt WHERE rerender_attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                assert row is not None
                return _rerender_attempt_from_row(row), True

    def mark_rerender_rendered(
        self,
        *,
        rerender_attempt_id: str,
        render_job_id: str,
        artifact_sha256: str | None,
        bounded_determinism_fingerprint: str | None,
        runtime_engine: str | None,
        runtime_engine_version: str | None,
        render_duration_ms: int | None,
    ) -> ReportRerenderAttemptRecord:
        with self._lock:
            with self._connect() as connection:
                return self._update_rerender_attempt(
                    connection=connection,
                    rerender_attempt_id=rerender_attempt_id,
                    status="rendered",
                    render_job_id=render_job_id,
                    artifact_sha256=artifact_sha256,
                    bounded_determinism_fingerprint=bounded_determinism_fingerprint,
                    runtime_engine=runtime_engine,
                    runtime_engine_version=runtime_engine_version,
                    render_duration_ms=render_duration_ms,
                )

    def mark_rerender_archiving(
        self,
        *,
        rerender_attempt_id: str,
        archive_request_id: str,
    ) -> ReportRerenderAttemptRecord:
        with self._lock:
            with self._connect() as connection:
                return self._update_rerender_attempt(
                    connection=connection,
                    rerender_attempt_id=rerender_attempt_id,
                    status="archiving",
                    archive_request_id=archive_request_id,
                )

    def mark_rerender_archived(
        self,
        *,
        rerender_attempt_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        archive_document_id: str,
    ) -> ReportRerenderAttemptRecord:
        with self._lock:
            with self._connect() as connection:
                archived = self._update_rerender_attempt(
                    connection=connection,
                    rerender_attempt_id=rerender_attempt_id,
                    status="archived",
                    archive_document_id=archive_document_id,
                    archive_completed_at=utc_now(),
                )
                self._append_status_event(
                    connection=connection,
                    job_id=archived.report_job_id,
                    from_status="archived",
                    to_status="archived",
                    event_type="job_rerender_archived",
                    message=(
                        f"Report rerender archived as correction document {archive_document_id}."
                    ),
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    created_at=utc_now(),
                )
                return archived

    def mark_rerender_failed(
        self,
        *,
        rerender_attempt_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        failure_category: str,
        failure_message: str,
        retry_eligible: bool,
    ) -> ReportRerenderAttemptRecord:
        with self._lock:
            with self._connect() as connection:
                failed = self._update_rerender_attempt(
                    connection=connection,
                    rerender_attempt_id=rerender_attempt_id,
                    status="failed",
                    failure_category=failure_category,
                    failure_message=failure_message,
                    retry_eligible=retry_eligible,
                )
                self._append_status_event(
                    connection=connection,
                    job_id=failed.report_job_id,
                    from_status="archived",
                    to_status="archived",
                    event_type="job_rerender_failed",
                    message=failure_message,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    created_at=utc_now(),
                )
                return failed

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
                    ,
                    job.render_job_id,
                    job.render_output_format,
                    job.render_template_id,
                    job.render_template_version,
                    job.render_artifact_sha256,
                    job.render_bounded_determinism_fingerprint,
                    job.render_runtime_engine,
                    job.render_runtime_engine_version,
                    job.render_duration_ms,
                    job.archive_request_id,
                    job.archive_document_id,
                    job.archive_completed_at
                FROM report_request req
                JOIN report_job job ON job.report_request_id = req.report_request_id
                ORDER BY job.created_at DESC, job.report_job_id DESC
                LIMIT ?
                """,
                (filters.limit,),
            ).fetchall()
        records = [_record_from_row(row) for row in rows]
        return [record for record in records if _record_matches_filters(record, filters)]

    def mark_collecting_data(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord:
        with self._lock:
            with self._connect() as connection:
                return self._transition_job(
                    connection=connection,
                    job_id=job_id,
                    allowed_from={"accepted"},
                    to_status="collecting_data",
                    failure_category=None,
                    failure_message=None,
                    current_step="collecting_data",
                    retry_eligible=0,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    event_type="job_collecting_data",
                    event_message="Portfolio review input capture started.",
                    set_started_at=True,
                    set_completed_at=False,
                    render_job_id=None,
                    render_output_format=None,
                    render_template_id=None,
                    render_template_version=None,
                    render_artifact_sha256=None,
                    render_bounded_determinism_fingerprint=None,
                    render_runtime_engine=None,
                    render_runtime_engine_version=None,
                    render_duration_ms=None,
                    archive_request_id=None,
                    archive_document_id=None,
                    archive_completed_at=None,
                )

    def mark_data_ready(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord:
        with self._lock:
            with self._connect() as connection:
                return self._transition_job(
                    connection=connection,
                    job_id=job_id,
                    allowed_from={"accepted", "collecting_data"},
                    to_status="data_ready",
                    failure_category=None,
                    failure_message=None,
                    current_step="data_ready",
                    retry_eligible=0,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    event_type="job_data_ready",
                    event_message="Portfolio review snapshot and lineage captured.",
                    set_started_at=True,
                    set_completed_at=False,
                    render_job_id=None,
                    render_output_format=None,
                    render_template_id=None,
                    render_template_version=None,
                    render_artifact_sha256=None,
                    render_bounded_determinism_fingerprint=None,
                    render_runtime_engine=None,
                    render_runtime_engine_version=None,
                    render_duration_ms=None,
                    archive_request_id=None,
                    archive_document_id=None,
                    archive_completed_at=None,
                )

    def mark_rendering(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        render_job_id: str,
        output_format: str,
        template_id: str,
        template_version: str,
    ) -> ReportJobLedgerRecord:
        with self._lock:
            with self._connect() as connection:
                return self._transition_job(
                    connection=connection,
                    job_id=job_id,
                    allowed_from={"data_ready"},
                    to_status="rendering",
                    failure_category=None,
                    failure_message=None,
                    current_step="rendering",
                    retry_eligible=0,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    event_type="job_rendering",
                    event_message="Portfolio review render started.",
                    set_started_at=True,
                    set_completed_at=False,
                    render_job_id=render_job_id,
                    render_output_format=output_format,
                    render_template_id=template_id,
                    render_template_version=template_version,
                    render_artifact_sha256=None,
                    render_bounded_determinism_fingerprint=None,
                    render_runtime_engine=None,
                    render_runtime_engine_version=None,
                    render_duration_ms=None,
                    archive_request_id=None,
                    archive_document_id=None,
                    archive_completed_at=None,
                )

    def mark_completed(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        render_job_id: str,
        output_format: str,
        template_id: str,
        template_version: str,
        artifact_sha256: str | None,
        bounded_determinism_fingerprint: str | None,
        runtime_engine: str | None,
        runtime_engine_version: str | None,
        render_duration_ms: int | None,
    ) -> ReportJobLedgerRecord:
        with self._lock:
            with self._connect() as connection:
                return self._transition_job(
                    connection=connection,
                    job_id=job_id,
                    allowed_from={"data_ready", "rendering"},
                    to_status="completed",
                    failure_category=None,
                    failure_message=None,
                    current_step="completed",
                    retry_eligible=0,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    event_type="job_completed",
                    event_message="Portfolio review render completed.",
                    set_started_at=True,
                    set_completed_at=True,
                    render_job_id=render_job_id,
                    render_output_format=output_format,
                    render_template_id=template_id,
                    render_template_version=template_version,
                    render_artifact_sha256=artifact_sha256,
                    render_bounded_determinism_fingerprint=bounded_determinism_fingerprint,
                    render_runtime_engine=runtime_engine,
                    render_runtime_engine_version=runtime_engine_version,
                    render_duration_ms=render_duration_ms,
                    archive_request_id=None,
                    archive_document_id=None,
                    archive_completed_at=None,
                )

    def mark_archiving(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        archive_request_id: str,
    ) -> ReportJobLedgerRecord:
        with self._lock:
            with self._connect() as connection:
                return self._transition_job(
                    connection=connection,
                    job_id=job_id,
                    allowed_from={"completed"},
                    to_status="archiving",
                    failure_category=None,
                    failure_message=None,
                    current_step="archiving",
                    retry_eligible=0,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    event_type="job_archiving",
                    event_message="Portfolio review archive handoff started.",
                    set_started_at=True,
                    set_completed_at=False,
                    render_job_id=None,
                    render_output_format=None,
                    render_template_id=None,
                    render_template_version=None,
                    render_artifact_sha256=None,
                    render_bounded_determinism_fingerprint=None,
                    render_runtime_engine=None,
                    render_runtime_engine_version=None,
                    render_duration_ms=None,
                    archive_request_id=archive_request_id,
                    archive_document_id=None,
                    archive_completed_at=None,
                )

    def mark_archived(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        archive_request_id: str,
        archive_document_id: str,
    ) -> ReportJobLedgerRecord:
        with self._lock:
            with self._connect() as connection:
                return self._transition_job(
                    connection=connection,
                    job_id=job_id,
                    allowed_from={"completed", "archiving"},
                    to_status="archived",
                    failure_category=None,
                    failure_message=None,
                    current_step="archived",
                    retry_eligible=0,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    event_type="job_archived",
                    event_message="Portfolio review archived successfully.",
                    set_started_at=True,
                    set_completed_at=False,
                    render_job_id=None,
                    render_output_format=None,
                    render_template_id=None,
                    render_template_version=None,
                    render_artifact_sha256=None,
                    render_bounded_determinism_fingerprint=None,
                    render_runtime_engine=None,
                    render_runtime_engine_version=None,
                    render_duration_ms=None,
                    archive_request_id=archive_request_id,
                    archive_document_id=archive_document_id,
                    archive_completed_at=utc_now(),
                )

    def mark_failed(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        failure_category: str,
        failure_message: str,
        retry_eligible: bool,
    ) -> ReportJobLedgerRecord:
        with self._lock:
            with self._connect() as connection:
                return self._transition_job(
                    connection=connection,
                    job_id=job_id,
                    allowed_from={
                        "accepted",
                        "collecting_data",
                        "data_ready",
                        "rendering",
                        "completed",
                        "archiving",
                    },
                    to_status="failed",
                    failure_category=failure_category,
                    failure_message=failure_message,
                    current_step="failed",
                    retry_eligible=1 if retry_eligible else 0,
                    actor=actor,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    event_type="job_failed",
                    event_message=failure_message,
                    set_started_at=True,
                    set_completed_at=True,
                    render_job_id=None,
                    render_output_format=None,
                    render_template_id=None,
                    render_template_version=None,
                    render_artifact_sha256=None,
                    render_bounded_determinism_fingerprint=None,
                    render_runtime_engine=None,
                    render_runtime_engine_version=None,
                    render_duration_ms=None,
                    archive_request_id=None,
                    archive_document_id=None,
                    archive_completed_at=None,
                )

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
                if current_status in {
                    "rendering",
                    "completed",
                    "archiving",
                    "archived",
                    "completed_with_warnings",
                    "cancelled",
                }:
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

    def _transition_job(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
        allowed_from: set[str],
        to_status: ReportJobStatus,
        failure_category: str | None,
        failure_message: str | None,
        current_step: str,
        retry_eligible: int,
        actor: str,
        correlation_id: str,
        trace_id: str,
        event_type: str,
        event_message: str | None,
        set_started_at: bool,
        set_completed_at: bool,
        render_job_id: str | None,
        render_output_format: str | None,
        render_template_id: str | None,
        render_template_version: str | None,
        render_artifact_sha256: str | None,
        render_bounded_determinism_fingerprint: str | None,
        render_runtime_engine: str | None,
        render_runtime_engine_version: str | None,
        render_duration_ms: int | None,
        archive_request_id: str | None,
        archive_document_id: str | None,
        archive_completed_at: datetime | None,
    ) -> ReportJobLedgerRecord:
        existing = connection.execute(
            "SELECT status, started_at FROM report_job WHERE report_job_id = ?",
            (job_id,),
        ).fetchone()
        if not existing:
            raise ReportJobNotFoundError("report_job_not_found")
        current_status = str(existing["status"])
        if current_status == to_status:
            row = connection.execute(
                "SELECT report_request_id FROM report_job WHERE report_job_id = ?",
                (job_id,),
            ).fetchone()
            assert row is not None
            return self._load_by_request_id(connection, row["report_request_id"])
        if current_status not in allowed_from:
            raise InvalidReportJobTransitionError("report_job_invalid_transition")

        now = utc_now()
        now_text = _dt_to_text(now)
        started_at = existing["started_at"] or (now_text if set_started_at else None)
        completed_at = now_text if set_completed_at else None
        connection.execute(
            """
            UPDATE report_job
            SET status = ?, failure_category = ?, failure_message = ?, current_step = ?,
                retry_eligible = ?, updated_at = ?, started_at = ?, completed_at = ?,
                render_job_id = COALESCE(?, render_job_id),
                render_output_format = COALESCE(?, render_output_format),
                render_template_id = COALESCE(?, render_template_id),
                render_template_version = COALESCE(?, render_template_version),
                render_artifact_sha256 = COALESCE(?, render_artifact_sha256),
                render_bounded_determinism_fingerprint = COALESCE(
                    ?,
                    render_bounded_determinism_fingerprint
                ),
                render_runtime_engine = COALESCE(?, render_runtime_engine),
                render_runtime_engine_version = COALESCE(?, render_runtime_engine_version),
                render_duration_ms = COALESCE(?, render_duration_ms),
                archive_request_id = COALESCE(?, archive_request_id),
                archive_document_id = COALESCE(?, archive_document_id),
                archive_completed_at = COALESCE(?, archive_completed_at)
            WHERE report_job_id = ?
            """,
            (
                to_status,
                failure_category,
                failure_message,
                current_step,
                retry_eligible,
                now_text,
                started_at,
                completed_at,
                render_job_id,
                render_output_format,
                render_template_id,
                render_template_version,
                render_artifact_sha256,
                render_bounded_determinism_fingerprint,
                render_runtime_engine,
                render_runtime_engine_version,
                render_duration_ms,
                archive_request_id,
                archive_document_id,
                _dt_to_text(archive_completed_at) if archive_completed_at else None,
                job_id,
            ),
        )
        self._append_status_event(
            connection=connection,
            job_id=job_id,
            from_status=current_status,
            to_status=to_status,
            event_type=event_type,
            message=event_message,
            actor=actor,
            correlation_id=correlation_id,
            trace_id=trace_id,
            created_at=now,
        )
        row = connection.execute(
            "SELECT report_request_id FROM report_job WHERE report_job_id = ?",
            (job_id,),
        ).fetchone()
        assert row is not None
        return self._load_by_request_id(connection, row["report_request_id"])

    def _update_rerender_attempt(
        self,
        *,
        connection: sqlite3.Connection,
        rerender_attempt_id: str,
        status: str,
        render_job_id: str | None = None,
        artifact_sha256: str | None = None,
        bounded_determinism_fingerprint: str | None = None,
        runtime_engine: str | None = None,
        runtime_engine_version: str | None = None,
        render_duration_ms: int | None = None,
        archive_request_id: str | None = None,
        archive_document_id: str | None = None,
        archive_completed_at: datetime | None = None,
        failure_category: str | None = None,
        failure_message: str | None = None,
        retry_eligible: bool | None = None,
    ) -> ReportRerenderAttemptRecord:
        existing = connection.execute(
            "SELECT * FROM report_rerender_attempt WHERE rerender_attempt_id = ?",
            (rerender_attempt_id,),
        ).fetchone()
        if not existing:
            raise ReportJobNotFoundError("report_rerender_attempt_not_found")
        now_text = _dt_to_text(utc_now())
        connection.execute(
            """
            UPDATE report_rerender_attempt
            SET status = ?,
                render_job_id = COALESCE(?, render_job_id),
                render_artifact_sha256 = COALESCE(?, render_artifact_sha256),
                render_bounded_determinism_fingerprint = COALESCE(
                    ?,
                    render_bounded_determinism_fingerprint
                ),
                render_runtime_engine = COALESCE(?, render_runtime_engine),
                render_runtime_engine_version = COALESCE(?, render_runtime_engine_version),
                render_duration_ms = COALESCE(?, render_duration_ms),
                archive_request_id = COALESCE(?, archive_request_id),
                archive_document_id = COALESCE(?, archive_document_id),
                archive_completed_at = COALESCE(?, archive_completed_at),
                failure_category = COALESCE(?, failure_category),
                failure_message = COALESCE(?, failure_message),
                retry_eligible = COALESCE(?, retry_eligible),
                updated_at = ?
            WHERE rerender_attempt_id = ?
            """,
            (
                status,
                render_job_id,
                artifact_sha256,
                bounded_determinism_fingerprint,
                runtime_engine,
                runtime_engine_version,
                render_duration_ms,
                archive_request_id,
                archive_document_id,
                _dt_to_text(archive_completed_at) if archive_completed_at else None,
                failure_category,
                failure_message,
                1 if retry_eligible else 0 if retry_eligible is not None else None,
                now_text,
                rerender_attempt_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM report_rerender_attempt WHERE rerender_attempt_id = ?",
            (rerender_attempt_id,),
        ).fetchone()
        assert row is not None
        return _rerender_attempt_from_row(row)

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
                job.cancelled_at,
                job.render_job_id,
                job.render_output_format,
                job.render_template_id,
                job.render_template_version,
                job.render_artifact_sha256,
                job.render_bounded_determinism_fingerprint,
                job.render_runtime_engine,
                job.render_runtime_engine_version,
                job.render_duration_ms,
                job.archive_request_id,
                job.archive_document_id,
                job.archive_completed_at
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
        render_job_id=_optional_row_value(row, "render_job_id"),
        render_output_format=_optional_row_value(row, "render_output_format"),
        render_template_id=_optional_row_value(row, "render_template_id"),
        render_template_version=_optional_row_value(row, "render_template_version"),
        render_artifact_sha256=_optional_row_value(row, "render_artifact_sha256"),
        render_bounded_determinism_fingerprint=_optional_row_value(
            row,
            "render_bounded_determinism_fingerprint",
        ),
        render_runtime_engine=_optional_row_value(row, "render_runtime_engine"),
        render_runtime_engine_version=_optional_row_value(row, "render_runtime_engine_version"),
        render_duration_ms=_optional_row_value(row, "render_duration_ms"),
        archive_request_id=_optional_row_value(row, "archive_request_id"),
        archive_document_id=_optional_row_value(row, "archive_document_id"),
        archive_completed_at=_dt_from_text(_optional_row_value(row, "archive_completed_at")),
    )


def _rerender_attempt_from_row(row: sqlite3.Row) -> ReportRerenderAttemptRecord:
    return ReportRerenderAttemptRecord(
        rerender_attempt_id=row["rerender_attempt_id"],
        report_job_id=row["report_job_id"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        snapshot_id=row["snapshot_id"],
        snapshot_hash=row["snapshot_hash"],
        previous_render_job_id=row["previous_render_job_id"],
        previous_archive_document_id=row["previous_archive_document_id"],
        render_job_id=row["render_job_id"],
        render_output_format=row["render_output_format"],
        render_template_id=row["render_template_id"],
        render_template_version=row["render_template_version"],
        render_artifact_sha256=row["render_artifact_sha256"],
        render_bounded_determinism_fingerprint=row["render_bounded_determinism_fingerprint"],
        render_runtime_engine=row["render_runtime_engine"],
        render_runtime_engine_version=row["render_runtime_engine_version"],
        render_duration_ms=row["render_duration_ms"],
        archive_request_id=row["archive_request_id"],
        archive_document_id=row["archive_document_id"],
        archive_completed_at=_dt_from_text(row["archive_completed_at"]),
        failure_category=row["failure_category"],
        failure_message=row["failure_message"],
        retry_eligible=bool(row["retry_eligible"]),
        requested_by=row["requested_by"],
        reason=row["reason"],
        correlation_id=row["correlation_id"],
        trace_id=row["trace_id"],
        created_at=_dt_from_text(row["created_at"]) or utc_now(),
        updated_at=_dt_from_text(row["updated_at"]) or utc_now(),
    )


def _optional_row_value(row: sqlite3.Row, key: str) -> Any | None:
    keys = row.keys() if hasattr(row, "keys") else row
    if key not in keys:
        return None
    return row[key]


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
