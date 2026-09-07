from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Iterator, Mapping
from uuid import uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.postgres import PostgresConnectionProvider
from app.report_batch_orchestrator.ledger import (
    BatchIdempotencyConflictError,
    MissingBatchIdempotencyKeyError,
    compute_batch_request_hash,
    utc_now,
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
from app.reporting_jobs.models import ReportCallerContext

if TYPE_CHECKING:
    from app.report_batch_orchestrator.schedule_definitions import (
        BatchScheduleAuditRecord,
        StoredBatchSchedule,
    )

from app.report_batch_orchestrator.ledger import (
    DuplicateScheduleDefinition,
    StaleScheduleRevision,
)
from app.reporting_persistence import ManagedPostgresAdapter, apply_report_schema_migrations


class PostgresReportBatchLedger(ManagedPostgresAdapter):
    """PostgreSQL-backed durable ledger for batch and batch-item materialization."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connection_provider: PostgresConnectionProvider | None = None,
    ) -> None:
        if connection_provider is None:
            if database_url is None:
                raise ValueError("report_batch_ledger_database_url_required")
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
                  AND table_name IN ('report_batch', 'report_batch_item')
                """
            ).fetchall()
            present = {str(row["table_name"]) for row in rows}
            missing = {"report_batch", "report_batch_item"} - present
            if missing:
                missing_names = ",".join(sorted(missing))
                raise RuntimeError(f"report_batch_ledger_schema_missing:{missing_names}")

    def has_batch_for_schedule_cycle(
        self,
        *,
        tenant_id: str,
        region: str,
        schedule_id: str,
        period_start: str,
        period_end: str,
        as_of_date: str,
    ) -> bool:
        """Whether this schedule's business cycle already has a batch.

        Recognition by the durable facts every scheduled batch records -
        tenant, region, schedule id, and period bounds - filtered in the
        database (JSONB operators; the as_of/tenant index keeps the
        per-pass cost bounded as the append-only ledger grows). Tenant and
        region participate because an operator-chosen schedule_id can recur
        across tenants: one tenant's batch must never suppress another
        tenant's cycle.
        """

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM report_batch
                WHERE as_of_date = %s
                  AND tenant_id = %s
                  AND region = %s
                  AND options_json->>'batch_schedule_id' = %s
                  AND options_json->>'batch_period_start' = %s
                  AND options_json->>'batch_period_end' = %s
                LIMIT 1
                """,
                (as_of_date, tenant_id, region, schedule_id, period_start, period_end),
            ).fetchone()
        return row is not None

    def has_batch_for_idempotency_key(self, idempotency_key: str) -> bool:
        """Whether ANY batch was materialized under this key.

        Used only for the legacy cycle-scope transition (report#283 finding
        E): a cycle materialized under the old template-bearing identity is
        recognised and skipped instead of re-materialized under the new
        business-cycle identity.
        """

        normalized_key = idempotency_key.strip()
        if not normalized_key:
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM report_batch WHERE idempotency_key = %s",
                (normalized_key,),
            ).fetchone()
        return row is not None

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
            status, retry_eligible = batch_item_failure_outcome(
                retryable=retryable,
                attempt_count=attempt_count,
                max_attempts=policy.max_attempts,
            )
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

    def relink_failed_item_for_replay(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
        replayed_report_job_id: str,
        retry_policy: BatchRetryPolicy | None = None,
        now: Any | None = None,
    ) -> ReportBatchItemRecord:
        replay_at = now or utc_now()
        policy = retry_policy or BatchRetryPolicy()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM report_batch_item
                WHERE batch_id = %s AND batch_item_id = %s
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
                    report_job_id = %s,
                    retry_eligible = FALSE,
                    next_retry_at = NULL,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_acquired_at = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat_at = NULL,
                    last_error_category = NULL,
                    last_error_summary = NULL,
                    started_at = COALESCE(started_at, %s),
                    completed_at = NULL,
                    cancelled_at = NULL
                WHERE batch_id = %s
                  AND batch_item_id = %s
                  AND status = 'failed_retryable'
                  AND retry_eligible IS TRUE
                  AND lease_token IS NULL
                  AND attempt_count < %s
                RETURNING *
                """,
                (replayed_report_job_id, replay_at, batch_id, batch_item_id, policy.max_attempts),
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
        return _count_from(row)

    def count_active_items(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM report_batch_item
                WHERE status IN ('leased', 'waiting_on_report_job')
                """
            ).fetchone()
        return _count_from(row)

    def batch_pressure_snapshot(self, *, now: Any | None = None) -> BatchPressureSnapshot:
        sample_at = now or utc_now()
        with self._connect() as connection:
            active_batches = _count_from(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM report_batch WHERE status = 'running'"
                ).fetchone()
            )
            active_items = _count_from(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM report_batch_item
                    WHERE status IN ('leased', 'waiting_on_report_job')
                    """
                ).fetchone()
            )
            dispatch_ready_items = _count_from(
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
                          AND lease_expires_at < %s
                        )
                        OR (
                          status = 'failed_retryable'
                          AND retry_eligible IS TRUE
                          AND (next_retry_at IS NULL OR next_retry_at <= %s)
                        )
                      )
                    """,
                    (sample_at, sample_at),
                ).fetchone()
            )
            retry_ready_items = _count_from(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM report_batch_item
                    WHERE status = 'failed_retryable'
                      AND retry_eligible IS TRUE
                      AND (next_retry_at IS NULL OR next_retry_at <= %s)
                    """,
                    (sample_at,),
                ).fetchone()
            )
            recovery_pending_items = _count_from(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM report_batch_item
                    WHERE status = 'recovery_pending'
                    """
                ).fetchone()
            )
            runnable_batches = _count_from(
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
                    ) runnable_batches
                    """,
                    (sample_at, sample_at),
                ).fetchone()
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
        tenant_ids: Sequence[str],
        limit: int = 10,
        now: Any | None = None,
    ) -> list[str]:
        """List runnable batches for the authorized tenant set only.

        tenant_ids is required rather than optional so a background caller cannot
        accidentally scan every tenant by omitting it, and an empty set selects
        nothing - there is no unscoped mode.
        """

        if limit < 1 or not tenant_ids:
            return []

        scan_at = now or utc_now()
        placeholders = ", ".join("%s" for _ in tenant_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT batch_id
                FROM (
                    SELECT
                        runnable.batch_id,
                        runnable.created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY runnable.tenant_id
                            ORDER BY runnable.created_at, runnable.batch_id
                        ) AS tenant_rank
                    FROM (
                SELECT DISTINCT
                    report_batch.batch_id,
                    report_batch.tenant_id,
                    report_batch.created_at
                FROM report_batch
                JOIN report_batch_item
                  ON report_batch_item.batch_id = report_batch.batch_id
                WHERE report_batch.tenant_id IN ({placeholders})
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
                    ) AS runnable
                ) AS ranked
                -- Round-robin across tenants: every tenant's oldest batch outranks
                -- any tenant's second-oldest, so one backlogged tenant cannot
                -- monopolize the bounded scan window. Ties within a rank rotate
                -- randomly per pass - stateless fairness when the authorized set
                -- is larger than the window, instead of a cursor to persist and
                -- repair. Within one tenant, age order is preserved by the rank.
                ORDER BY tenant_rank, random()
                LIMIT %s
                """,
                (*tenant_ids, scan_at, scan_at, limit),
            ).fetchall()
        return [str(row["batch_id"]) for row in rows]

    def list_attention_batch_ids(
        self,
        *,
        limit: int = 100,
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
                      'leased',
                      'waiting_on_report_job',
                      'recovery_pending'
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
                (scan_at, limit),
            ).fetchall()
        return [str(row["batch_id"]) for row in rows]

    def save_schedule_definition(self, schedule: "StoredBatchSchedule") -> "StoredBatchSchedule":
        try:
            with self._connect() as connection:
                self._write_schedule_definition(connection, schedule)
        except DuplicateScheduleDefinition as exc:
            raise self._resolved_duplicate(exc, schedule) from exc
        return schedule

    def _resolved_duplicate(
        self,
        exc: DuplicateScheduleDefinition,
        schedule: "StoredBatchSchedule",
    ) -> DuplicateScheduleDefinition:
        if exc.existing_schedule_id:
            return exc
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT schedule_id FROM report_batch_schedule_definition
                WHERE fingerprint = %s AND enabled
                """,
                (schedule.fingerprint,),
            ).fetchone()
        return DuplicateScheduleDefinition(row["schedule_id"] if row else schedule.schedule_id)

    def save_schedule_definition_with_audit(
        self,
        schedule: "StoredBatchSchedule",
        record: "BatchScheduleAuditRecord",
    ) -> "StoredBatchSchedule":
        """Definition and audit event in one transaction - a schedule must never
        exist without the audit record that explains it."""
        try:
            with self._connect() as connection:
                self._write_schedule_definition(connection, schedule)
                self._write_schedule_audit(connection, record)
        except DuplicateScheduleDefinition as exc:
            raise self._resolved_duplicate(exc, schedule) from exc
        return schedule

    def _write_schedule_definition(self, connection: Any, schedule: "StoredBatchSchedule") -> None:
        if schedule.revision == 1:
            try:
                connection.execute(
                    """
                    INSERT INTO report_batch_schedule_definition (
                        schedule_id, tenant_id, region, booking_center_code, owner_actor,
                        enabled, cadence, portfolio_ids_json,
                        requested_output_formats_json, reporting_currency, options_json,
                        max_batch_size, fingerprint, cadence_effective_on, revision,
                        created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        schedule.schedule_id,
                        schedule.tenant_id,
                        schedule.region,
                        schedule.booking_center_code,
                        schedule.owner_actor,
                        schedule.enabled,
                        schedule.cadence,
                        Jsonb(schedule.portfolio_ids),
                        Jsonb(schedule.requested_output_formats),
                        schedule.reporting_currency,
                        Jsonb(schedule.options),
                        schedule.max_batch_size,
                        schedule.fingerprint,
                        schedule.cadence_effective_on,
                        schedule.revision,
                        schedule.created_at,
                        schedule.updated_at,
                    ),
                )
            except UniqueViolation as exc:
                constraint = getattr(getattr(exc, "diag", None), "constraint_name", "")
                if constraint != "uq_report_batch_schedule_fingerprint_enabled":
                    raise
                # Winner resolution happens on a fresh connection in the public
                # wrapper, after this failed transaction releases its connection -
                # a pool of size one must not deadlock against itself.
                raise DuplicateScheduleDefinition("") from exc
            return
        try:
            result = connection.execute(
                """
            UPDATE report_batch_schedule_definition SET
                enabled = %s,
                cadence = %s,
                portfolio_ids_json = %s,
                requested_output_formats_json = %s,
                reporting_currency = %s,
                options_json = %s,
                max_batch_size = %s,
                fingerprint = %s,
                cadence_effective_on = %s,
                revision = %s,
                updated_at = %s
            WHERE schedule_id = %s AND revision = %s
            """,
                (
                    schedule.enabled,
                    schedule.cadence,
                    Jsonb(schedule.portfolio_ids),
                    Jsonb(schedule.requested_output_formats),
                    schedule.reporting_currency,
                    Jsonb(schedule.options),
                    schedule.max_batch_size,
                    schedule.fingerprint,
                    schedule.cadence_effective_on,
                    schedule.revision,
                    schedule.updated_at,
                    schedule.schedule_id,
                    schedule.revision - 1,
                ),
            )
        except UniqueViolation as exc:
            constraint = getattr(getattr(exc, "diag", None), "constraint_name", "")
            if constraint != "uq_report_batch_schedule_fingerprint_enabled":
                raise
            raise DuplicateScheduleDefinition(schedule.schedule_id) from exc
        if result.rowcount != 1:
            raise StaleScheduleRevision(schedule.schedule_id)

    def get_schedule_definition(self, schedule_id: str) -> "StoredBatchSchedule | None":
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_batch_schedule_definition WHERE schedule_id = %s",
                (schedule_id,),
            ).fetchone()
        if row is None:
            return None
        return _schedule_definition_from_row(row)

    def list_schedule_definitions(self, tenant_id: str) -> "list[StoredBatchSchedule]":
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM report_batch_schedule_definition
                WHERE tenant_id = %s
                ORDER BY created_at, schedule_id
                """,
                (tenant_id,),
            ).fetchall()
        return [_schedule_definition_from_row(row) for row in rows]

    def append_schedule_audit(
        self, record: "BatchScheduleAuditRecord"
    ) -> "BatchScheduleAuditRecord":
        with self._connect() as connection:
            self._write_schedule_audit(connection, record)
        return record

    def _write_schedule_audit(self, connection: Any, record: "BatchScheduleAuditRecord") -> None:
        connection.execute(
            """
                INSERT INTO report_batch_schedule_audit (
                    audit_id, schedule_id, action, actor, correlation_id,
                    changes_json, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
            (
                record.audit_id,
                record.schedule_id,
                record.action,
                record.actor,
                record.correlation_id,
                Jsonb(record.changes),
                record.created_at,
            ),
        )

    def list_schedule_audit(self, schedule_id: str) -> "list[BatchScheduleAuditRecord]":
        with self._connect() as connection:
            return self._read_schedule_audit(connection, schedule_id)

    def get_schedule_definition_with_audit(
        self, schedule_id: str
    ) -> "tuple[StoredBatchSchedule | None, list[BatchScheduleAuditRecord]]":
        """Definition and audit in one transaction, so the pair cannot straddle a
        concurrent update."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_batch_schedule_definition WHERE schedule_id = %s",
                (schedule_id,),
            ).fetchone()
            if row is None:
                return None, []
            return _schedule_definition_from_row(row), self._read_schedule_audit(
                connection, schedule_id
            )

    def _read_schedule_audit(
        self, connection: Any, schedule_id: str
    ) -> "list[BatchScheduleAuditRecord]":
        rows = connection.execute(
            """
            SELECT * FROM report_batch_schedule_audit
            WHERE schedule_id = %s
            ORDER BY audit_sequence
            """,
            (schedule_id,),
        ).fetchall()
        return [_schedule_audit_from_row(row) for row in rows]

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
                WHERE batch_id = %s AND batch_item_id = %s
                """,
                (batch_id, batch_item_id),
            ).fetchone()
            if not row:
                raise ValueError("report_batch_item_not_found")
            return _item_from_row(row)

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
        status = reconciled_batch_status(str(row["status"]) for row in rows)
        if status is None:
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
        booking_center_code=(
            str(batch_row["booking_center_code"])
            if batch_row["booking_center_code"] is not None
            else None
        ),
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


def _count_from(row: Mapping[str, Any] | None) -> int:
    """The count from an aggregate row, refusing a shape that cannot occur.

    ``SELECT COUNT(*)`` without ``GROUP BY`` always returns exactly one row, so
    ``fetchone()`` is never ``None`` at these call sites. Stating that here
    keeps the reasoning in one place instead of eight, and turns a future query
    or driver change that breaks the assumption into a named failure rather
    than ``'NoneType' object is not subscriptable`` several frames away.
    """

    if row is None:
        raise ValueError("aggregate_count_query_returned_no_row")
    return int(row["count"])


def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _schedule_definition_from_row(row: Any) -> "StoredBatchSchedule":
    from app.report_batch_orchestrator.schedule_definitions import StoredBatchSchedule

    return StoredBatchSchedule(
        schedule_id=row["schedule_id"],
        tenant_id=row["tenant_id"],
        region=row["region"],
        booking_center_code=row["booking_center_code"],
        owner_actor=row["owner_actor"],
        enabled=row["enabled"],
        cadence=row["cadence"],
        portfolio_ids=list(row["portfolio_ids_json"]),
        requested_output_formats=list(row["requested_output_formats_json"]),
        reporting_currency=row["reporting_currency"],
        options=dict(row["options_json"]),
        max_batch_size=row["max_batch_size"],
        cadence_effective_on=row["cadence_effective_on"],
        revision=row["revision"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _schedule_audit_from_row(row: Any) -> "BatchScheduleAuditRecord":
    from app.report_batch_orchestrator.schedule_definitions import BatchScheduleAuditRecord

    return BatchScheduleAuditRecord(
        audit_id=row["audit_id"],
        schedule_id=row["schedule_id"],
        action=row["action"],
        actor=row["actor"],
        correlation_id=row["correlation_id"],
        changes=dict(row["changes_json"]),
        created_at=row["created_at"],
    )
