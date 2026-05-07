from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

import psycopg
from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobNotFoundError,
    _request_parts,
    compute_request_hash,
    utc_now,
)
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

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


class PostgresReportJobLedger:
    """PostgreSQL-backed runtime ledger for report request/job/status lifecycle state."""

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
                  AND table_name IN ('report_request', 'report_job', 'report_status_event')
                """
            ).fetchall()
            present = {str(row["table_name"]) for row in rows}
            missing = {"report_request", "report_job", "report_status_event"} - present
            if missing:
                raise RuntimeError(f"report_job_ledger_schema_missing:{','.join(sorted(missing))}")

            archive_column_rows = connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'report_job'
                  AND column_name IN (
                      'archive_request_id',
                      'archive_document_id',
                      'archive_completed_at'
                  )
                """
            ).fetchall()
            archive_columns = {str(row["column_name"]) for row in archive_column_rows}
            missing_archive_columns = {
                "archive_request_id",
                "archive_document_id",
                "archive_completed_at",
            } - archive_columns
            if missing_archive_columns:
                raise RuntimeError(
                    "report_job_ledger_archive_schema_missing:"
                    f"{','.join(sorted(missing_archive_columns))}"
                )
            rerender_rows = connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'report_rerender_attempt'
                """
            ).fetchall()
            if not rerender_rows:
                raise RuntimeError("report_rerender_attempt_schema_missing")

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

        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT report_request_id, request_hash
                    FROM report_request
                    WHERE idempotency_key = %s
                    """,
                    (normalized_key,),
                ).fetchone()
                if existing:
                    return self._existing_or_conflict(connection, existing, request_hash)

                now = utc_now()
                request_id = f"rrq_{uuid4().hex}"
                job_id = f"rjob_{uuid4().hex}"

                connection.execute(
                    """
                    INSERT INTO report_request (
                        report_request_id, report_type, portfolio_scope_json,
                        requested_output_formats_json, as_of_date, reporting_currency,
                        options_json, trigger_type, triggered_by, caller_application,
                        tenant_id, region, booking_center_code, role, idempotency_key,
                        request_hash, correlation_id, trace_id, created_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        request_id,
                        report_type,
                        Jsonb(portfolio_scope),
                        Jsonb(sorted(output_formats)),
                        as_of_date,
                        reporting_currency,
                        Jsonb(options),
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
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO report_job (
                        report_job_id, report_request_id, report_type, portfolio_scope_json,
                        status, failure_category, failure_message, current_step, retry_eligible,
                        cancel_requested, created_at, updated_at, started_at, completed_at,
                        cancelled_at, render_job_id, render_output_format, render_template_id,
                        render_template_version, render_artifact_sha256,
                        render_bounded_determinism_fingerprint, render_runtime_engine,
                        render_runtime_engine_version, render_duration_ms,
                        archive_request_id, archive_document_id, archive_completed_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        job_id,
                        request_id,
                        report_type,
                        Jsonb(portfolio_scope),
                        "accepted",
                        None,
                        None,
                        "accepted",
                        False,
                        False,
                        now,
                        now,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
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
        except UniqueViolation as exc:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT report_request_id, request_hash
                    FROM report_request
                    WHERE idempotency_key = %s
                    """,
                    (normalized_key,),
                ).fetchone()
                if existing:
                    return self._existing_or_conflict(connection, existing, request_hash)
            raise IdempotencyConflictError("idempotency_key_unique_violation") from exc

    def get_job(self, job_id: str) -> ReportJobLedgerRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_request_id FROM report_job WHERE report_job_id = %s",
                (job_id,),
            ).fetchone()
            if not row:
                raise ReportJobNotFoundError("report_job_not_found")
            return self._load_by_request_id(connection, str(row["report_request_id"]))

    def list_status_events(self, job_id: str) -> list[ReportStatusEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM report_status_event
                WHERE report_job_id = %s
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
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT status FROM report_job WHERE report_job_id = %s FOR UPDATE",
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
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM report_rerender_attempt
                WHERE report_job_id = %s AND idempotency_key = %s
                """,
                (job.job_id, normalized_key),
            ).fetchone()
            if existing:
                return _rerender_attempt_from_row(existing), False
            now = utc_now()
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
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
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
                    False,
                    actor,
                    reason,
                    correlation_id,
                    trace_id,
                    now,
                    now,
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
                "SELECT * FROM report_rerender_attempt WHERE rerender_attempt_id = %s",
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
                message=f"Report rerender archived as correction document {archive_document_id}.",
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
        where_clauses = ["1=1"]
        params: list[Any] = []

        if filters.tenant_id:
            where_clauses.append("req.tenant_id = %s")
            params.append(filters.tenant_id)
        if filters.region:
            where_clauses.append("req.region = %s")
            params.append(filters.region)
        if filters.status:
            where_clauses.append("job.status = %s")
            params.append(filters.status)
        if filters.report_type:
            where_clauses.append("req.report_type = %s")
            params.append(filters.report_type)
        if filters.portfolio_id:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        req.portfolio_scope_json -> 'portfolio_ids'
                    ) AS pid(value)
                    WHERE pid.value = %s
                )
                """
            )
            params.append(filters.portfolio_id)
        if filters.as_of_date:
            where_clauses.append("req.as_of_date = %s")
            params.append(filters.as_of_date)
        if filters.idempotency_key:
            where_clauses.append("req.idempotency_key = %s")
            params.append(filters.idempotency_key)
        if filters.correlation_id:
            where_clauses.append("req.correlation_id = %s")
            params.append(filters.correlation_id)
        if filters.created_from:
            where_clauses.append("job.created_at >= %s")
            params.append(filters.created_from)
        if filters.created_to:
            where_clauses.append("job.created_at <= %s")
            params.append(filters.created_to)

        query = f"""
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
            WHERE {" AND ".join(where_clauses)}
            ORDER BY job.created_at DESC, job.report_job_id DESC
            LIMIT %s
        """
        params.append(filters.limit)

        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_record_from_row(row) for row in rows]

    def mark_collecting_data(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord:
        with self._connect() as connection:
            return self._transition_job(
                connection=connection,
                job_id=job_id,
                allowed_from={"accepted"},
                to_status="collecting_data",
                failure_category=None,
                failure_message=None,
                current_step="collecting_data",
                retry_eligible=False,
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
        with self._connect() as connection:
            return self._transition_job(
                connection=connection,
                job_id=job_id,
                allowed_from={"accepted", "collecting_data"},
                to_status="data_ready",
                failure_category=None,
                failure_message=None,
                current_step="data_ready",
                retry_eligible=False,
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
        with self._connect() as connection:
            return self._transition_job(
                connection=connection,
                job_id=job_id,
                allowed_from={"data_ready"},
                to_status="rendering",
                failure_category=None,
                failure_message=None,
                current_step="rendering",
                retry_eligible=False,
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
        with self._connect() as connection:
            return self._transition_job(
                connection=connection,
                job_id=job_id,
                allowed_from={"data_ready", "rendering"},
                to_status="completed",
                failure_category=None,
                failure_message=None,
                current_step="completed",
                retry_eligible=False,
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
        with self._connect() as connection:
            return self._transition_job(
                connection=connection,
                job_id=job_id,
                allowed_from={"completed"},
                to_status="archiving",
                failure_category=None,
                failure_message=None,
                current_step="archiving",
                retry_eligible=False,
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
        with self._connect() as connection:
            return self._transition_job(
                connection=connection,
                job_id=job_id,
                allowed_from={"completed", "archiving"},
                to_status="archived",
                failure_category=None,
                failure_message=None,
                current_step="archived",
                retry_eligible=False,
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
                retry_eligible=retry_eligible,
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
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT status FROM report_job WHERE report_job_id = %s FOR UPDATE",
                (job_id,),
            ).fetchone()
            if not existing:
                raise ReportJobNotFoundError("report_job_not_found")
            current_status = str(existing["status"])
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
            connection.execute(
                """
                UPDATE report_job
                SET status = %s, failure_category = %s, failure_message = %s, current_step = %s,
                    retry_eligible = %s, cancel_requested = %s, updated_at = %s, cancelled_at = %s
                WHERE report_job_id = %s
                """,
                (
                    "cancelled",
                    "cancelled",
                    "Report job cancelled before render or archive processing.",
                    "cancelled",
                    False,
                    True,
                    now,
                    now,
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
                "SELECT report_request_id FROM report_job WHERE report_job_id = %s",
                (job_id,),
            ).fetchone()
            if not row:
                raise ReportJobNotFoundError("report_job_not_found")
            return self._load_by_request_id(connection, str(row["report_request_id"]))

    def _existing_or_conflict(
        self,
        connection: Connection[Mapping[str, Any]],
        existing: Mapping[str, Any],
        request_hash: str,
    ) -> ReportJobLedgerRecord:
        if existing["request_hash"] != request_hash:
            raise IdempotencyConflictError("idempotency_key_reused_with_different_request")
        return self._load_by_request_id(connection, str(existing["report_request_id"]))

    def _append_status_event(
        self,
        *,
        connection: Connection[Mapping[str, Any]],
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"rse_{uuid4().hex}",
                job_id,
                from_status,
                to_status,
                event_type,
                message,
                actor,
                created_at,
                correlation_id,
                trace_id,
            ),
        )

    def _transition_job(
        self,
        *,
        connection: Connection[Mapping[str, Any]],
        job_id: str,
        allowed_from: set[str],
        to_status: ReportJobStatus,
        failure_category: str | None,
        failure_message: str | None,
        current_step: str,
        retry_eligible: bool,
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
            "SELECT status, started_at FROM report_job WHERE report_job_id = %s FOR UPDATE",
            (job_id,),
        ).fetchone()
        if not existing:
            raise ReportJobNotFoundError("report_job_not_found")
        current_status = str(existing["status"])
        if current_status == to_status:
            row = connection.execute(
                "SELECT report_request_id FROM report_job WHERE report_job_id = %s",
                (job_id,),
            ).fetchone()
            assert row is not None
            return self._load_by_request_id(connection, str(row["report_request_id"]))
        if current_status not in allowed_from:
            raise InvalidReportJobTransitionError("report_job_invalid_transition")

        now = utc_now()
        started_at = existing["started_at"] or (now if set_started_at else None)
        completed_at = now if set_completed_at else None
        connection.execute(
            """
            UPDATE report_job
            SET status = %s, failure_category = %s, failure_message = %s, current_step = %s,
                retry_eligible = %s, updated_at = %s, started_at = %s, completed_at = %s,
                render_job_id = COALESCE(%s, render_job_id),
                render_output_format = COALESCE(%s, render_output_format),
                render_template_id = COALESCE(%s, render_template_id),
                render_template_version = COALESCE(%s, render_template_version),
                render_artifact_sha256 = COALESCE(%s, render_artifact_sha256),
                render_bounded_determinism_fingerprint = COALESCE(
                    %s,
                    render_bounded_determinism_fingerprint
                ),
                render_runtime_engine = COALESCE(%s, render_runtime_engine),
                render_runtime_engine_version = COALESCE(%s, render_runtime_engine_version),
                render_duration_ms = COALESCE(%s, render_duration_ms),
                archive_request_id = COALESCE(%s, archive_request_id),
                archive_document_id = COALESCE(%s, archive_document_id),
                archive_completed_at = COALESCE(%s, archive_completed_at)
            WHERE report_job_id = %s
            """,
            (
                to_status,
                failure_category,
                failure_message,
                current_step,
                retry_eligible,
                now,
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
                archive_completed_at,
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
            "SELECT report_request_id FROM report_job WHERE report_job_id = %s",
            (job_id,),
        ).fetchone()
        assert row is not None
        return self._load_by_request_id(connection, str(row["report_request_id"]))

    def _update_rerender_attempt(
        self,
        *,
        connection: Connection[Mapping[str, Any]],
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
            "SELECT * FROM report_rerender_attempt WHERE rerender_attempt_id = %s FOR UPDATE",
            (rerender_attempt_id,),
        ).fetchone()
        if not existing:
            raise ReportJobNotFoundError("report_rerender_attempt_not_found")
        connection.execute(
            """
            UPDATE report_rerender_attempt
            SET status = %s,
                render_job_id = COALESCE(%s, render_job_id),
                render_artifact_sha256 = COALESCE(%s, render_artifact_sha256),
                render_bounded_determinism_fingerprint = COALESCE(
                    %s,
                    render_bounded_determinism_fingerprint
                ),
                render_runtime_engine = COALESCE(%s, render_runtime_engine),
                render_runtime_engine_version = COALESCE(%s, render_runtime_engine_version),
                render_duration_ms = COALESCE(%s, render_duration_ms),
                archive_request_id = COALESCE(%s, archive_request_id),
                archive_document_id = COALESCE(%s, archive_document_id),
                archive_completed_at = COALESCE(%s, archive_completed_at),
                failure_category = COALESCE(%s, failure_category),
                failure_message = COALESCE(%s, failure_message),
                retry_eligible = COALESCE(%s, retry_eligible),
                updated_at = %s
            WHERE rerender_attempt_id = %s
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
                archive_completed_at,
                failure_category,
                failure_message,
                retry_eligible,
                utc_now(),
                rerender_attempt_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM report_rerender_attempt WHERE rerender_attempt_id = %s",
            (rerender_attempt_id,),
        ).fetchone()
        assert row is not None
        return _rerender_attempt_from_row(row)

    def _load_by_request_id(
        self,
        connection: Connection[Mapping[str, Any]],
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
            WHERE req.report_request_id = %s
            """,
            (request_id,),
        ).fetchone()
        if not row:
            raise ReportJobNotFoundError("report_job_not_found")
        return _record_from_row(row)


def _dt_from_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _date_from_value(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _record_from_row(row: Mapping[str, Any]) -> ReportJobLedgerRecord:
    return ReportJobLedgerRecord(
        request_id=str(row["report_request_id"]),
        job_id=str(row["report_job_id"]),
        report_type=str(row["report_type"]),
        portfolio_scope=dict(row["request_portfolio_scope_json"]),
        requested_output_formats=list(row["requested_output_formats_json"]),
        as_of_date=_date_from_value(row["as_of_date"]),
        reporting_currency=row["reporting_currency"],
        options=dict(row["options_json"]),
        trigger_type=str(row["trigger_type"]),
        triggered_by=str(row["triggered_by"]),
        caller_application=str(row["caller_application"]),
        tenant_id=str(row["tenant_id"]),
        region=str(row["region"]),
        booking_center_code=row["booking_center_code"],
        role=row["role"],
        idempotency_key=str(row["idempotency_key"]),
        request_hash=str(row["request_hash"]),
        status=row["status"],
        failure_category=row["failure_category"],
        failure_message=row["failure_message"],
        current_step=str(row["current_step"]),
        retry_eligible=bool(row["retry_eligible"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=_dt_from_value(row["job_created_at"]) or utc_now(),
        updated_at=_dt_from_value(row["updated_at"]) or utc_now(),
        started_at=_dt_from_value(row["started_at"]),
        completed_at=_dt_from_value(row["completed_at"]),
        cancelled_at=_dt_from_value(row["cancelled_at"]),
        correlation_id=str(row["correlation_id"]),
        trace_id=str(row["trace_id"]),
        render_job_id=row.get("render_job_id"),
        render_output_format=row.get("render_output_format"),
        render_template_id=row.get("render_template_id"),
        render_template_version=row.get("render_template_version"),
        render_artifact_sha256=row.get("render_artifact_sha256"),
        render_bounded_determinism_fingerprint=row.get("render_bounded_determinism_fingerprint"),
        render_runtime_engine=row.get("render_runtime_engine"),
        render_runtime_engine_version=row.get("render_runtime_engine_version"),
        render_duration_ms=row.get("render_duration_ms"),
        archive_request_id=row.get("archive_request_id"),
        archive_document_id=row.get("archive_document_id"),
        archive_completed_at=_dt_from_value(row.get("archive_completed_at")),
    )


def _rerender_attempt_from_row(row: Mapping[str, Any]) -> ReportRerenderAttemptRecord:
    return ReportRerenderAttemptRecord(
        rerender_attempt_id=str(row["rerender_attempt_id"]),
        report_job_id=str(row["report_job_id"]),
        idempotency_key=str(row["idempotency_key"]),
        status=row["status"],
        snapshot_id=str(row["snapshot_id"]),
        snapshot_hash=str(row["snapshot_hash"]),
        previous_render_job_id=row.get("previous_render_job_id"),
        previous_archive_document_id=row.get("previous_archive_document_id"),
        render_job_id=str(row["render_job_id"]),
        render_output_format=str(row["render_output_format"]),
        render_template_id=str(row["render_template_id"]),
        render_template_version=str(row["render_template_version"]),
        render_artifact_sha256=row.get("render_artifact_sha256"),
        render_bounded_determinism_fingerprint=row.get("render_bounded_determinism_fingerprint"),
        render_runtime_engine=row.get("render_runtime_engine"),
        render_runtime_engine_version=row.get("render_runtime_engine_version"),
        render_duration_ms=row.get("render_duration_ms"),
        archive_request_id=row.get("archive_request_id"),
        archive_document_id=row.get("archive_document_id"),
        archive_completed_at=_dt_from_value(row.get("archive_completed_at")),
        failure_category=row.get("failure_category"),
        failure_message=row.get("failure_message"),
        retry_eligible=bool(row["retry_eligible"]),
        requested_by=str(row["requested_by"]),
        reason=str(row["reason"]),
        correlation_id=str(row["correlation_id"]),
        trace_id=str(row["trace_id"]),
        created_at=_dt_from_value(row["created_at"]) or utc_now(),
        updated_at=_dt_from_value(row["updated_at"]) or utc_now(),
    )


def _event_from_row(row: Mapping[str, Any]) -> ReportStatusEvent:
    return ReportStatusEvent(
        status_event_id=str(row["status_event_id"]),
        report_job_id=str(row["report_job_id"]),
        from_status=row["from_status"],
        to_status=row["to_status"],
        event_type=str(row["event_type"]),
        message=row["message"],
        actor=str(row["actor"]),
        created_at=_dt_from_value(row["created_at"]) or utc_now(),
        correlation_id=str(row["correlation_id"]),
        trace_id=str(row["trace_id"]),
    )
