from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.report_batch_orchestrator.models import ReportBatchRecord
from app.reporting_jobs.models import ReportJobLedgerRecord, ReportJobListFilters
from app.reporting_metrics import record_attention_scan_metrics, record_report_operation
from app.reporting_operations.models import (
    AttentionSeverity,
    AttentionType,
    ReportingAttentionEvent,
    ReportingAttentionScanResponse,
)

ACTIVE_REPORT_JOB_STATUSES = (
    "accepted",
    "queued",
    "collecting_data",
    "data_ready",
    "rendering",
    "archiving",
)
ACTIVE_BATCH_ITEM_STATUSES = (
    "materialized",
    "leased",
    "waiting_on_report_job",
    "recovery_pending",
)


class ReportJobLedgerPort(Protocol):
    def list_jobs(self, *, filters: ReportJobListFilters) -> list[ReportJobLedgerRecord]: ...


class ReportBatchLedgerPort(Protocol):
    def list_attention_batch_ids(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> list[str]: ...

    def get_batch(self, batch_id: str) -> ReportBatchRecord: ...


@dataclass(frozen=True)
class AttentionScanConfig:
    report_job_stuck_threshold_seconds: int = 900
    batch_item_stuck_threshold_seconds: int = 900
    sla_breach_threshold_seconds: int = 3600
    max_report_jobs_per_status: int = 100
    max_batches: int = 100
    max_events: int = 250

    def __post_init__(self) -> None:
        if self.report_job_stuck_threshold_seconds < 1:
            raise ValueError("invalid_report_job_stuck_threshold_seconds")
        if self.batch_item_stuck_threshold_seconds < 1:
            raise ValueError("invalid_batch_item_stuck_threshold_seconds")
        if self.sla_breach_threshold_seconds < 1:
            raise ValueError("invalid_sla_breach_threshold_seconds")
        if self.max_report_jobs_per_status < 1:
            raise ValueError("invalid_max_report_jobs_per_status")
        if self.max_batches < 1:
            raise ValueError("invalid_max_batches")
        if self.max_events < 1:
            raise ValueError("invalid_max_events")


class ReportingAttentionScanner:
    def __init__(
        self,
        *,
        report_job_ledger: ReportJobLedgerPort,
        batch_ledger: ReportBatchLedgerPort,
        config: AttentionScanConfig | None = None,
    ) -> None:
        self._report_job_ledger = report_job_ledger
        self._batch_ledger = batch_ledger
        self._config = config or AttentionScanConfig()

    def scan(self, *, now: datetime | None = None) -> ReportingAttentionScanResponse:
        scan_at = _as_utc(now or datetime.now(UTC))
        events = [
            *self._report_job_events(scan_at=scan_at),
            *self._batch_item_events(scan_at=scan_at),
        ]
        events = sorted(
            events,
            key=lambda event: (
                0 if event.severity == "critical" else 1,
                -event.age_seconds,
                event.resource_type,
                event.resource_id,
            ),
        )[: self._config.max_events]

        response = ReportingAttentionScanResponse(
            scan_id=f"rasc_{scan_at.strftime('%Y%m%dT%H%M%SZ')}",
            scanned_at=scan_at,
            report_job_stuck_threshold_seconds=(self._config.report_job_stuck_threshold_seconds),
            batch_item_stuck_threshold_seconds=(self._config.batch_item_stuck_threshold_seconds),
            sla_breach_threshold_seconds=self._config.sla_breach_threshold_seconds,
            event_count=len(events),
            events=events,
        )
        record_report_operation(
            operation="stuck_state_scan",
            status="completed",
            failure_category=None,
        )
        record_attention_scan_metrics(events)
        return response

    def _report_job_events(self, *, scan_at: datetime) -> list[ReportingAttentionEvent]:
        events: list[ReportingAttentionEvent] = []
        for job_status in ACTIVE_REPORT_JOB_STATUSES:
            records = self._report_job_ledger.list_jobs(
                filters=ReportJobListFilters(
                    status=job_status,
                    limit=self._config.max_report_jobs_per_status,
                )
            )
            for record in records:
                age_seconds = _elapsed_seconds(scan_at, record.updated_at)
                if age_seconds < min(
                    self._config.report_job_stuck_threshold_seconds,
                    self._config.sla_breach_threshold_seconds,
                ):
                    continue
                attention_type, severity, threshold_seconds = self._classify(
                    age_seconds=age_seconds,
                    stuck_threshold_seconds=self._config.report_job_stuck_threshold_seconds,
                )
                events.append(
                    ReportingAttentionEvent(
                        resource_type="report_job",
                        resource_id=record.job_id,
                        parent_resource_id=None,
                        attention_type=attention_type,
                        severity=severity,
                        status=record.status,
                        reason=_report_job_reason(attention_type),
                        age_seconds=age_seconds,
                        threshold_seconds=threshold_seconds,
                        recommended_action=(
                            "Inspect report job diagnostics and replay only if the source "
                            "failure is retryable."
                        ),
                        evidence_url=f"/reports/jobs/{record.job_id}/diagnostics",
                        observed_at=scan_at,
                    )
                )
        return events

    def _batch_item_events(self, *, scan_at: datetime) -> list[ReportingAttentionEvent]:
        events: list[ReportingAttentionEvent] = []
        batch_ids = self._batch_ledger.list_attention_batch_ids(
            limit=self._config.max_batches,
            now=scan_at,
        )
        for batch_id in batch_ids:
            batch = self._batch_ledger.get_batch(batch_id)
            for item in batch.items:
                if item.status not in ACTIVE_BATCH_ITEM_STATUSES:
                    continue
                last_activity = (
                    item.last_heartbeat_at
                    or item.dispatched_at
                    or item.lease_acquired_at
                    or item.started_at
                    or item.created_at
                )
                age_seconds = _elapsed_seconds(scan_at, last_activity)
                if age_seconds < min(
                    self._config.batch_item_stuck_threshold_seconds,
                    self._config.sla_breach_threshold_seconds,
                ):
                    continue
                attention_type, severity, threshold_seconds = self._classify(
                    age_seconds=age_seconds,
                    stuck_threshold_seconds=self._config.batch_item_stuck_threshold_seconds,
                )
                events.append(
                    ReportingAttentionEvent(
                        resource_type="batch_item",
                        resource_id=item.batch_item_id,
                        parent_resource_id=batch.batch_id,
                        attention_type=attention_type,
                        severity=severity,
                        status=item.status,
                        reason=_batch_item_reason(attention_type, item.status),
                        age_seconds=age_seconds,
                        threshold_seconds=threshold_seconds,
                        recommended_action=(
                            "Inspect the batch item, recover expired leases, and replay only "
                            "after the linked report job failure is confirmed retryable."
                        ),
                        evidence_url=(
                            f"/reports/batches/{batch.batch_id}/items/{item.batch_item_id}"
                        ),
                        observed_at=scan_at,
                    )
                )
        return events

    def _classify(
        self,
        *,
        age_seconds: int,
        stuck_threshold_seconds: int,
    ) -> tuple[AttentionType, AttentionSeverity, int]:
        if age_seconds >= self._config.sla_breach_threshold_seconds:
            return "sla_breach", "critical", self._config.sla_breach_threshold_seconds
        return "stuck_state", "warning", stuck_threshold_seconds


def _elapsed_seconds(scan_at: datetime, since: datetime) -> int:
    return max(0, int((scan_at - _as_utc(since)).total_seconds()))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _report_job_reason(attention_type: AttentionType) -> str:
    if attention_type == "sla_breach":
        return "report_job_active_state_exceeded_sla_threshold"
    return "report_job_active_state_exceeded_stuck_threshold"


def _batch_item_reason(attention_type: AttentionType, status: str) -> str:
    if attention_type == "sla_breach":
        return "batch_item_active_state_exceeded_sla_threshold"
    if status == "leased":
        return "batch_item_lease_heartbeat_stale"
    if status == "waiting_on_report_job":
        return "batch_item_waiting_on_report_job_exceeded_stuck_threshold"
    return "batch_item_active_state_exceeded_stuck_threshold"
