"""Fenced operations on the existing Report job/work rows, shared by both ledgers."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from secrets import randbelow
from typing import Any

from app.reporting_jobs.custody_policy import (
    ENCODING_VERSION,
    MAX_OFFER_BYTES,
    MAX_RETAINED_BYTES,
    MAX_TENANT_BYTES,
    RECOVERY_DELAYS,
    REQUIRED_POLICY,
    CustodyBindings,
    CustodyLease,
    CustodyRecoveryError,
    as_datetime,
    current_work_lease,
    custody_protected,
    encode_render_offer,
    offer_digest,
)

_JOB_PROJECTION = """j.report_job_id, j.report_request_id, j.report_type, j.status, j.render_job_id,
    j.render_template_id, j.render_template_version,
    j.archive_request_id, j.archive_document_id, j.custody_policy, j.custody_state,
    j.render_offer_sha256, j.render_offer_size, j.render_offer_version,
    j.render_offer_generation, j.custody_epoch, j.custody_initial_count,
    j.custody_dispatch_count, j.custody_poll_count, j.custody_window_started_at,
    j.custody_next_attempt_at, j.custody_reason, r.tenant_id, r.region,
    r.correlation_id, r.trace_id"""


class CustodyRecovery:
    def __init__(self, ledger: Any, *, postgres: bool, bindings: CustodyBindings | None):
        self.ledger = ledger
        self.postgres = postgres
        self.bindings = bindings

    def execute(self, connection: Any, sql: str, values: tuple[Any, ...] = ()) -> Any:
        result = connection.execute(sql.replace("?", "%s") if self.postgres else sql, values)
        if sql.lstrip().upper().startswith("UPDATE ") and result.rowcount != 1:
            raise CustodyRecoveryError("custody_stale_write")
        return result

    def timestamp(self, value: datetime) -> Any:
        return value if self.postgres else value.astimezone(UTC).isoformat()

    def begin(self, connection: Any) -> None:
        if not self.postgres and not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")

    def authorize(self, operation: str, job_id: str, tenant_id: str, region: str) -> None:
        if self.bindings is None:
            raise CustodyRecoveryError("custody_authority_unavailable")
        self.bindings.require_readiness()
        self.bindings.authorize(
            operation=operation, job_id=job_id, tenant_id=tenant_id, region=region
        )

    def acceptance_policy(self, report_type: str, tenant_id: str, region: str) -> str | None:
        if self.bindings is None:
            return None
        self.bindings.require_readiness()
        return (
            REQUIRED_POLICY
            if self.bindings.admit(report_type=report_type, tenant_id=tenant_id, region=region)
            else None
        )

    def metadata(self, connection: Any, job_id: str) -> Any:
        row = self.execute(
            connection,
            f"SELECT {_JOB_PROJECTION} FROM report_job j JOIN report_request r "
            "ON r.report_request_id = j.report_request_id WHERE j.report_job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise CustodyRecoveryError("custody_job_not_found")
        return row

    def observe(self, job_id: str) -> dict[str, Any]:
        with self.ledger._connect() as connection:
            return dict(self.metadata(connection, job_id))

    def lock_pair(self, connection: Any, job_id: str) -> tuple[Any, Any]:
        self.begin(connection)
        if self.postgres:
            connection.execute("SET LOCAL lock_timeout = '5s'")
        suffix = " FOR UPDATE" if self.postgres else ""
        work = self.execute(
            connection,
            "SELECT * FROM report_job_work_item WHERE report_job_id = ?" + suffix,
            (job_id,),
        ).fetchone()
        # Legacy jobs may have no work item. Required jobs must always retain theirs.
        job = self.execute(
            connection,
            "SELECT custody_policy FROM report_job WHERE report_job_id = ?" + suffix,
            (job_id,),
        ).fetchone()
        if job is None:
            raise CustodyRecoveryError("custody_job_not_found")
        row = self.metadata(connection, job_id)
        if row["custody_policy"] == REQUIRED_POLICY and work is None:
            raise CustodyRecoveryError("custody_work_missing")
        return work, row

    def clock(self, connection: Any, fallback: datetime) -> datetime:
        if self.postgres:
            instant = as_datetime(
                connection.execute("SELECT clock_timestamp() AS instant").fetchone()["instant"]
            )
            if instant is None:
                raise CustodyRecoveryError("custody_database_clock_unavailable")
            return instant
        return fallback

    def lease(
        self, connection: Any, work: Any, row: Any, fence: CustodyLease, now: datetime
    ) -> None:
        now = self.clock(connection, now)
        if (
            work is None
            or work["work_item_id"] != fence.work_item_id
            or work["status"] != "leased"
            or work["lease_token"] != fence.token
            or row["custody_epoch"] != fence.epoch
            or (as_datetime(work["lease_expires_at"]) or now) <= now
        ):
            raise CustodyRecoveryError("custody_stale_lease")

    def event(self, connection: Any, row: Any, reason: str) -> None:
        self.ledger._append_status_event(
            connection=connection,
            job_id=row["report_job_id"],
            from_status=row["status"],
            to_status=row["status"],
            event_type="job_custody_parked",
            message=reason[:240],
            event_payload={"reason_code": reason[:80]},
            actor="report-job-worker",
            correlation_id=row["correlation_id"],
            trace_id=row["trace_id"],
            created_at=datetime.now(UTC),
        )

    def park(self, connection: Any, work: Any, row: Any, reason: str) -> None:
        if row["custody_state"] == "verified":
            return
        if work is None:
            raise CustodyRecoveryError("custody_work_missing")
        result = self.execute(
            connection,
            "UPDATE report_job_work_item SET status='failed', lease_token=NULL, "
            "lease_owner=NULL, lease_acquired_at=NULL, lease_expires_at=NULL, "
            "last_error_category=?, last_error_summary=?, updated_at=? "
            "WHERE work_item_id=? AND status=?",
            (
                reason[:80],
                "Custody unresolved; automatic work parked.",
                self.timestamp(datetime.now(UTC)),
                work["work_item_id"],
                work["status"],
            ),
        )
        if result.rowcount != 1:
            raise CustodyRecoveryError("custody_stale_park")
        self.execute(
            connection,
            "UPDATE report_job SET custody_state='blocked', custody_reason=?, "
            "custody_epoch=custody_epoch+1 WHERE report_job_id=? AND custody_epoch=?",
            (reason[:80], row["report_job_id"], row["custody_epoch"]),
        )
        self.event(connection, row, reason)

    def guard_transition(self, connection: Any, job_id: str, to_status: str) -> bool:
        found = self.execute(
            connection, "SELECT custody_policy FROM report_job WHERE report_job_id=?", (job_id,)
        ).fetchone()
        if found is None or found["custody_policy"] is None:
            return False  # Preserve the legacy ledger's not-found/status contracts.
        work, row = self.lock_pair(connection, job_id)
        if row["custody_policy"] != REQUIRED_POLICY:
            return False
        identity = current_work_lease.get()
        if identity is not None:
            self.lease(
                connection,
                work,
                row,
                CustodyLease(identity[0], identity[1], row["custody_epoch"]),
                datetime.now(UTC),
            )
        elif to_status in {"collecting_data", "data_ready"}:
            raise CustodyRecoveryError("custody_worker_lease_required")
        if row["custody_state"] == "verified":
            return to_status != "archived"
        if custody_protected(row) and to_status in {"failed", "cancelled", "archived"}:
            self.park(connection, work, row, "custody_transition_blocked")
            return True
        if to_status in {"rendering", "completed", "archiving"}:
            # Only dedicated fenced ports advance required jobs.
            raise CustodyRecoveryError("custody_fenced_transition_required")
        return False

    def scheduled_at(self, connection: Any, work_id: str, proposed: datetime) -> datetime:
        selected = self.execute(
            connection,
            "SELECT report_job_id FROM report_job_work_item WHERE work_item_id=?",
            (work_id,),
        ).fetchone()
        row = self.metadata(connection, selected["report_job_id"])
        due = as_datetime(row["custody_next_attempt_at"])
        return max(proposed, due) if due and row["custody_policy"] == REQUIRED_POLICY else proposed

    def protect_work(self, connection: Any, work_id: str, *, completing: bool = False) -> bool:
        selected = self.execute(
            connection,
            "SELECT report_job_id FROM report_job_work_item WHERE work_item_id=?",
            (work_id,),
        ).fetchone()
        if selected is None:
            raise CustodyRecoveryError("custody_work_missing")
        work, row = self.lock_pair(connection, selected["report_job_id"])
        if row["custody_policy"] == REQUIRED_POLICY and row["custody_state"] == "verified":
            self.execute(
                connection,
                "UPDATE report_job_work_item SET status='completed', lease_owner=NULL, "
                "lease_token=NULL, lease_acquired_at=NULL, lease_expires_at=NULL "
                "WHERE work_item_id=?",
                (work_id,),
            )
            return True
        if custody_protected(row):
            self.park(
                connection,
                work,
                row,
                "custody_completion_blocked" if completing else "custody_retry_exhausted",
            )
            return True
        return False

    def validate_work(self, connection: Any, work_id: str, token: str, now: datetime) -> None:
        self.begin(connection)
        found = self.execute(
            connection,
            "SELECT report_job_id FROM report_job_work_item WHERE work_item_id=?",
            (work_id,),
        ).fetchone()
        if found is None:
            return
        work, row = self.lock_pair(connection, found["report_job_id"])
        if row["custody_policy"] == REQUIRED_POLICY:
            self.lease(
                connection, work, row, CustodyLease(work_id, token, row["custody_epoch"]), now
            )

    def settle_verified_work(self, connection: Any, work_id: str) -> Any:
        row = self.execute(
            connection,
            "SELECT j.custody_state FROM report_job j JOIN report_job_work_item w "
            "ON w.report_job_id=j.report_job_id WHERE w.work_item_id=?",
            (work_id,),
        ).fetchone()
        if row is None or row["custody_state"] != "verified":
            return None
        self.protect_work(connection, work_id)
        return self.execute(
            connection, "SELECT * FROM report_job_work_item WHERE work_item_id=?", (work_id,)
        ).fetchone()

    def claimed(self, connection: Any, work: Any, lease_seconds: int) -> None:
        locked_work, row = self.lock_pair(connection, work["report_job_id"])
        if row["custody_policy"] == REQUIRED_POLICY:
            if lease_seconds < 120:
                raise CustodyRecoveryError("custody_lease_budget_invalid")
            self.lease(
                connection,
                locked_work,
                row,
                CustodyLease(work["work_item_id"], work["lease_token"], row["custody_epoch"]),
                datetime.now(UTC),
            )
            self.execute(
                connection,
                "UPDATE report_job SET custody_epoch=custody_epoch+1 WHERE report_job_id=?",
                (work["report_job_id"],),
            )

    def commit_offer(
        self,
        *,
        job_id: str,
        tenant_id: str,
        region: str,
        fence: CustodyLease,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> str:
        self.authorize("retain_offer", job_id, tenant_id, region)
        body = encode_render_offer(payload)
        digest = offer_digest(body)
        render_id = payload.get("render_job_id")
        if not isinstance(render_id, str) or not render_id:
            raise CustodyRecoveryError("custody_render_identity_missing")
        with self.ledger._connect() as connection:
            work, row = self.lock_pair(connection, job_id)
            self.lease(connection, work, row, fence, now or datetime.now(UTC))
            self.scope(row, tenant_id, region)
            if row["render_offer_sha256"]:
                if row["render_offer_sha256"] != digest or row["render_job_id"] != render_id:
                    raise CustodyRecoveryError("custody_offer_conflict")
                return digest
            self.offer_identity(row, payload)
            if row["status"] != "data_ready":
                raise CustodyRecoveryError("custody_offer_not_ready")
            if self.postgres:
                connection.execute("SELECT pg_advisory_xact_lock(446028)")
                self.lease(connection, work, row, fence, datetime.now(UTC))
            sums = self.execute(
                connection,
                "SELECT COALESCE(SUM(length(j.render_offer_body)),0) AS total, "
                "COALESCE(SUM(CASE WHEN r.tenant_id=? THEN length(j.render_offer_body) "
                "ELSE 0 END),0) AS tenant FROM report_job j JOIN report_request r "
                "ON r.report_request_id=j.report_request_id",
                (tenant_id,),
            ).fetchone()
            if sums["total"] + len(body) > MAX_RETAINED_BYTES or (
                sums["tenant"] + len(body) > MAX_TENANT_BYTES
            ):
                raise CustodyRecoveryError("custody_retention_quota_exceeded")
            result = self.execute(
                connection,
                "UPDATE report_job SET render_offer_body=?, render_offer_sha256=?, "
                "render_offer_size=?, render_offer_version=?, render_offer_generation=1, "
                "render_job_id=?, status='rendering', custody_state='ready' "
                "WHERE report_job_id=? AND custody_policy=? AND render_offer_body IS NULL",
                (body, digest, len(body), ENCODING_VERSION, render_id, job_id, REQUIRED_POLICY),
            )
            if result.rowcount != 1:
                raise CustodyRecoveryError("custody_offer_commit_conflict")
            self.ledger._append_status_event(
                connection=connection,
                job_id=job_id,
                from_status="data_ready",
                to_status="rendering",
                event_type="job_rendering",
                message="Retained Render offer.",
                event_payload={
                    "render_output_format": payload["output_format"],
                    "render_template_id": payload["template_id"],
                },
                actor="report-job-worker",
                correlation_id=row["correlation_id"],
                trace_id=row["trace_id"],
                created_at=now or datetime.now(UTC),
            )
        return digest

    def offer_identity(self, row: Any, payload: dict[str, Any]) -> None:
        output = payload.get("output_format")
        archive = payload.get("render_context", {}).get("archive", {})
        expected = {
            "report_job_id": row["report_job_id"],
            "report_type": row["report_type"],
            "render_job_id": f"rdr_{row['report_job_id']}_{output}",
            "template_id": row["render_template_id"],
            "template_version": row["render_template_version"],
        }
        if output not in {"pdf", "xlsx"} or any(payload.get(k) != v for k, v in expected.items()):
            raise CustodyRecoveryError("custody_offer_identity_mismatch")
        if archive.get("tenant_id") != row["tenant_id"] or archive.get("region") != row["region"]:
            raise CustodyRecoveryError("custody_offer_scope_mismatch")

    def scope(self, row: Any, tenant_id: str, region: str) -> None:
        if (
            row["tenant_id"] != tenant_id
            or row["region"] != region
            or (row["custody_policy"] != REQUIRED_POLICY)
        ):
            raise CustodyRecoveryError("custody_scope_mismatch")

    def load_offer(self, *, job_id: str, tenant_id: str, region: str) -> bytes:
        self.authorize("read_offer", job_id, tenant_id, region)
        with self.ledger._connect() as connection:
            row = self.metadata(connection, job_id)
            self.scope(row, tenant_id, region)
            stored = self.execute(
                connection,
                "SELECT render_offer_body FROM report_job WHERE report_job_id=?",
                (job_id,),
            ).fetchone()
            body = bytes(stored["render_offer_body"] or b"")
            if not 0 < len(body) <= MAX_OFFER_BYTES or (
                row["render_offer_size"] != len(body)
                or row["render_offer_sha256"] != offer_digest(body)
                or row["render_offer_version"] != ENCODING_VERSION
                or row["render_offer_generation"] != 1
            ):
                raise CustodyRecoveryError("custody_offer_integrity_failed")
            return body

    def reserve(
        self,
        *,
        job_id: str,
        tenant_id: str,
        region: str,
        fence: CustodyLease,
        operation: str,
        now: datetime | None = None,
    ) -> bool:
        if operation not in {"initial", "retry", "poll"}:
            raise CustodyRecoveryError("custody_operation_invalid")
        self.authorize(operation, job_id, tenant_id, region)
        instant = now or datetime.now(UTC)
        with self.ledger._connect() as connection:
            work, row = self.lock_pair(connection, job_id)
            instant = self.clock(connection, instant)
            self.lease(connection, work, row, fence, instant)
            self.scope(row, tenant_id, region)
            window = as_datetime(row["custody_window_started_at"]) or instant
            due = as_datetime(row["custody_next_attempt_at"]) or instant
            if row["custody_state"] in {"blocked", "refused", "verified"} or due > instant:
                return False
            column, limit = {
                "initial": ("custody_initial_count", 1),
                "retry": ("custody_dispatch_count", 3),
                "poll": ("custody_poll_count", 12),
            }[operation]
            if row[column] >= limit or instant >= window + timedelta(hours=24):
                self.park(connection, work, row, "custody_retry_budget_exhausted")
                return False
            next_recovery = row["custody_dispatch_count"] + (operation == "retry")
            delay = 0 if operation == "poll" else RECOVERY_DELAYS[min(next_recovery, 2)]
            if operation != "poll":
                delay += randbelow(11)
            self.execute(
                connection,
                f"UPDATE report_job SET {column}={column}+1, custody_window_started_at=?, "
                "custody_next_attempt_at=? WHERE report_job_id=? AND custody_epoch=?",
                (
                    self.timestamp(window),
                    self.timestamp(instant + timedelta(seconds=delay)),
                    job_id,
                    row["custody_epoch"],
                ),
            )

        return True

    def disposition(
        self,
        *,
        job_id: str,
        fence: CustodyLease,
        response: dict[str, Any],
        http_status: int,
        tenant_id: str,
        region: str,
    ) -> None:
        self.authorize("record_outcome", job_id, tenant_id, region)
        with self.ledger._connect() as connection:
            work, row = self.lock_pair(connection, job_id)
            self.lease(connection, work, row, fence, datetime.now(UTC))
            self.scope(row, tenant_id, region)
            returned_id = response.get("render_job_id")
            if returned_id is not None and returned_id != row["render_job_id"]:
                self.park(connection, work, row, "custody_identity_conflict")
                return
            request_id = response.get("archive_request_id")
            if row["archive_request_id"] and request_id not in {None, row["archive_request_id"]}:
                self.park(connection, work, row, "custody_identity_conflict")
                return
            state = response.get("archive_state")
            if (
                400 <= http_status < 500
                and http_status != 429
                or (isinstance(state, str) and state.startswith("archive_refused_"))
            ):
                self.park(connection, work, row, "custody_refused")
                self.execute(
                    connection,
                    "UPDATE report_job SET custody_state='refused' WHERE report_job_id=?",
                    (job_id,),
                )
                return
            if request_id and isinstance(request_id, str) and len(request_id) <= 256:
                self.execute(
                    connection,
                    "UPDATE report_job SET archive_request_id=?, status='archiving' "
                    "WHERE report_job_id=?",
                    (request_id, job_id),
                )
            # Existing archived_verified means a named response only. No release/verified
            # transition is implemented without an agreed native verification binding.
            self.execute(
                connection,
                "UPDATE report_job SET custody_state='waiting' WHERE report_job_id=?",
                (job_id,),
            )

    def headers(
        self, *, operation: str, job_id: str, tenant_id: str, region: str
    ) -> dict[str, str]:
        self.authorize(operation, job_id, tenant_id, region)
        assert self.bindings is not None
        return self.bindings.transport_headers(
            operation=operation, job_id=job_id, tenant_id=tenant_id, region=region
        )

    def block(self, *, job_id: str, tenant_id: str, region: str, fence: CustodyLease) -> None:
        self.authorize("record_outcome", job_id, tenant_id, region)
        with self.ledger._connect() as connection:
            work, row = self.lock_pair(connection, job_id)
            self.lease(connection, work, row, fence, datetime.now(UTC))
            self.scope(row, tenant_id, region)
            self.park(connection, work, row, "custody_owner_blocked")

    def record_owner_due(
        self, *, job_id: str, tenant_id: str, region: str, fence: CustodyLease, due: datetime
    ) -> bool:
        self.authorize("record_owner_due", job_id, tenant_id, region)
        instant = datetime.now(UTC)
        target = as_datetime(due)
        if target is None:
            raise CustodyRecoveryError("custody_due_invalid")
        with self.ledger._connect() as connection:
            work, row = self.lock_pair(connection, job_id)
            instant = self.clock(connection, instant)
            self.lease(connection, work, row, fence, instant)
            self.scope(row, tenant_id, region)
            window = as_datetime(row["custody_window_started_at"]) or instant
            if target >= window + timedelta(hours=24):
                self.park(connection, work, row, "custody_owner_schedule_exhausted")
                return False
            target = max(target, as_datetime(row["custody_next_attempt_at"]) or instant)
            self.execute(
                connection,
                "UPDATE report_job SET custody_next_attempt_at=? WHERE report_job_id=?",
                (self.timestamp(target), job_id),
            )
        return True

    async def confirm_final(
        self,
        *,
        job_id: str,
        tenant_id: str,
        region: str,
        fence: CustodyLease,
        response: dict[str, Any],
    ) -> None:
        self.authorize("verify_final", job_id, tenant_id, region)
        identity = {
            key: response.get(key)
            for key in (
                "render_job_id",
                "archive_request_id",
                "archive_document_id",
                "artifact_sha256",
            )
        }
        if not all(isinstance(value, str) and 0 < len(value) <= 256 for value in identity.values()):
            raise CustodyRecoveryError("custody_verification_identity_missing")
        identity.update(job_id=job_id, tenant_id=tenant_id, region=region)
        original = self.observe(job_id)
        self.scope(original, tenant_id, region)
        if (
            original["render_job_id"] != identity["render_job_id"]
            or original["archive_request_id"] != identity["archive_request_id"]
        ):
            raise CustodyRecoveryError("custody_verification_identity_conflict")
        identity.update(
            offer_sha256=original["render_offer_sha256"],
            offer_generation=original["render_offer_generation"],
            epoch=fence.epoch,
        )
        assert self.bindings is not None
        # This port must independently authorize/read native Archive metadata AND
        # bytes. The named Render response is not proof. Production has no binding.
        async with asyncio.timeout(10):
            await self.bindings.verify_final_custody(identity=identity)
        with self.ledger._connect() as connection:
            work, row = self.lock_pair(connection, job_id)
            self.lease(connection, work, row, fence, datetime.now(UTC))
            self.scope(row, tenant_id, region)
            if (
                row["render_job_id"] != identity["render_job_id"]
                or row["archive_request_id"] != identity["archive_request_id"]
            ):
                raise CustodyRecoveryError("custody_verification_identity_conflict")
            proof = json.dumps(
                {
                    **identity,
                    "offer_sha256": row["render_offer_sha256"],
                    "offer_generation": row["render_offer_generation"],
                    "epoch": fence.epoch,
                },
                allow_nan=False,
            )
            self.execute(
                connection,
                "UPDATE report_job SET custody_state='verified', status='archived', "
                "archive_document_id=?, custody_verification_json=?, archive_completed_at=?, "
                "completed_at=?, updated_at=?, current_step='archived' WHERE report_job_id=?",
                (
                    identity["archive_document_id"],
                    proof,
                    self.timestamp(self.clock(connection, datetime.now(UTC))),
                    self.timestamp(self.clock(connection, datetime.now(UTC))),
                    self.timestamp(self.clock(connection, datetime.now(UTC))),
                    job_id,
                ),
            )
            self.ledger._append_status_event(
                connection=connection,
                job_id=job_id,
                from_status=row["status"],
                to_status="archived",
                event_type="job_archived",
                message="Native custody independently verified.",
                event_payload={"archive_document_id": identity["archive_document_id"]},
                actor="report-job-worker",
                correlation_id=row["correlation_id"],
                trace_id=row["trace_id"],
                created_at=self.clock(connection, datetime.now(UTC)),
            )
            # Body retained even after final verification; no cleanup/transfer port.

    def resume(
        self,
        *,
        job_id: str,
        tenant_id: str,
        region: str,
        expected_epoch: int,
        expected_digest: str,
        now: datetime | None = None,
    ) -> None:
        self.authorize("resume", job_id, tenant_id, region)
        instant = now or datetime.now(UTC)
        with self.ledger._connect() as connection:
            work, row = self.lock_pair(connection, job_id)
            self.scope(row, tenant_id, region)
            instant = self.clock(connection, instant)
            if (
                work["status"] == "retry_pending"
                and row["custody_epoch"] == expected_epoch + 1
                and row["render_offer_sha256"] == expected_digest
                and row["custody_state"] == "ready"
            ):
                return
            if (
                work["status"] != "failed"
                or row["custody_epoch"] != expected_epoch
                or row["render_offer_sha256"] != expected_digest
                or row["custody_state"] != "blocked"
                or row["custody_reason"]
                not in {"custody_retry_budget_exhausted", "custody_retry_exhausted"}
            ):
                raise CustodyRecoveryError("custody_resume_conflict")
            self.execute(
                connection,
                "UPDATE report_job SET custody_state='ready', custody_epoch="
                "custody_epoch+1, custody_dispatch_count=0, custody_poll_count=0, "
                "custody_window_started_at=?, custody_next_attempt_at=? "
                "WHERE report_job_id=?",
                (self.timestamp(instant), self.timestamp(instant), job_id),
            )
            self.execute(
                connection,
                "UPDATE report_job_work_item SET status='retry_pending', attempt_count=0, "
                "available_at=? "
                "WHERE work_item_id=?",
                (self.timestamp(instant), work["work_item_id"]),
            )
