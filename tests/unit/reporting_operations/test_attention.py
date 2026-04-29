from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    PortfolioBatchCandidate,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.reporting_jobs.ledger import ReportJobLedger, _dt_to_text
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
    ReportJobListFilters,
)
from app.reporting_operations.attention import AttentionScanConfig, ReportingAttentionScanner

SCAN_AT = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
OLD_AT = datetime(2026, 4, 28, 10, 0, tzinfo=UTC)


def _caller() -> ReportCallerContext:
    return ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id="corr-attention",
        trace_id="trace-attention",
    )


def _report_request() -> PortfolioReviewJobRequest:
    return PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        as_of_date="2026-04-22",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


def _batch_request() -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id="PB_SG_GLOBAL_BAL_001",
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
                selected=True,
            )
        ],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


def test_attention_scan_flags_stuck_report_job_without_sensitive_payloads(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch_ledger = ReportBatchLedger(tmp_path / "batches.sqlite3")
    record = report_ledger.create_portfolio_review_job(
        request=_report_request(),
        caller_context=_caller(),
        idempotency_key="attention-report-job",
    )
    _set_report_job_updated_at(report_ledger, record.job_id, OLD_AT)

    scanner = ReportingAttentionScanner(
        report_job_ledger=report_ledger,
        batch_ledger=batch_ledger,
        config=AttentionScanConfig(
            report_job_stuck_threshold_seconds=1,
            batch_item_stuck_threshold_seconds=1,
            sla_breach_threshold_seconds=3600,
        ),
    )

    response = scanner.scan(now=SCAN_AT)

    assert response.event_count == 1
    event = response.events[0]
    assert event.resource_type == "report_job"
    assert event.resource_id == record.job_id
    assert event.attention_type == "sla_breach"
    assert event.severity == "critical"
    assert event.reason == "report_job_active_state_exceeded_sla_threshold"
    assert event.evidence_url == f"/reports/jobs/{record.job_id}/diagnostics"
    serialized = response.model_dump_json()
    assert "PB_SG_GLOBAL_BAL_001" not in serialized
    assert "tenant-sg" not in serialized
    assert "corr-attention" not in serialized
    assert "trace-attention" not in serialized


def test_attention_scan_flags_stale_non_expired_batch_item_heartbeat(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch_ledger = ReportBatchLedger(tmp_path / "batches.sqlite3")
    batch = batch_ledger.create_batch(
        request=_batch_request(),
        caller_context=_caller(),
        idempotency_key="attention-batch",
    )
    [leased_item] = batch_ledger.acquire_dispatch_items(
        batch_id=batch.batch_id,
        worker_id="worker-1",
        lease_seconds=7200,
        limit=1,
        now=datetime(2026, 4, 28, 11, 0, tzinfo=UTC),
    )

    scanner = ReportingAttentionScanner(
        report_job_ledger=report_ledger,
        batch_ledger=batch_ledger,
        config=AttentionScanConfig(
            report_job_stuck_threshold_seconds=900,
            batch_item_stuck_threshold_seconds=300,
            sla_breach_threshold_seconds=7200,
        ),
    )

    response = scanner.scan(now=SCAN_AT)

    assert response.event_count == 1
    event = response.events[0]
    assert event.resource_type == "batch_item"
    assert event.resource_id == leased_item.batch_item_id
    assert event.parent_resource_id == batch.batch_id
    assert event.status == "leased"
    assert event.attention_type == "stuck_state"
    assert event.severity == "warning"
    assert event.reason == "batch_item_lease_heartbeat_stale"
    assert event.evidence_url == (
        f"/reports/batches/{batch.batch_id}/items/{leased_item.batch_item_id}"
    )


def test_attention_scan_respects_max_events_and_critical_sorting(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch_ledger = ReportBatchLedger(tmp_path / "batches.sqlite3")
    for index in range(3):
        report_ledger.create_portfolio_review_job(
            request=_report_request(),
            caller_context=_caller(),
            idempotency_key=f"attention-sort-{index}",
        )
    for record in report_ledger.list_jobs(filters=ReportJobListFilters(limit=100)):
        _set_report_job_updated_at(report_ledger, record.job_id, OLD_AT)

    scanner = ReportingAttentionScanner(
        report_job_ledger=report_ledger,
        batch_ledger=batch_ledger,
        config=AttentionScanConfig(
            report_job_stuck_threshold_seconds=1,
            batch_item_stuck_threshold_seconds=1,
            sla_breach_threshold_seconds=1,
            max_events=2,
        ),
    )

    response = scanner.scan(now=SCAN_AT)

    assert response.event_count == 2
    assert {event.severity for event in response.events} == {"critical"}


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("report_job_stuck_threshold_seconds", "invalid_report_job_stuck_threshold_seconds"),
        ("batch_item_stuck_threshold_seconds", "invalid_batch_item_stuck_threshold_seconds"),
        ("sla_breach_threshold_seconds", "invalid_sla_breach_threshold_seconds"),
        ("max_report_jobs_per_status", "invalid_max_report_jobs_per_status"),
        ("max_batches", "invalid_max_batches"),
        ("max_events", "invalid_max_events"),
    ],
)
def test_attention_scan_config_rejects_invalid_bounds(field: str, message: str) -> None:
    kwargs = {field: 0}

    with pytest.raises(ValueError, match=message):
        AttentionScanConfig(**kwargs)


def test_attention_scan_skips_non_stuck_records_and_normalizes_naive_scan_time(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch_ledger = ReportBatchLedger(tmp_path / "batches.sqlite3")
    record = report_ledger.create_portfolio_review_job(
        request=_report_request(),
        caller_context=_caller(),
        idempotency_key="attention-non-stuck-report-job",
    )
    _set_report_job_updated_at(report_ledger, record.job_id, SCAN_AT)

    scanner = ReportingAttentionScanner(
        report_job_ledger=report_ledger,
        batch_ledger=batch_ledger,
        config=AttentionScanConfig(
            report_job_stuck_threshold_seconds=60,
            batch_item_stuck_threshold_seconds=60,
            sla_breach_threshold_seconds=120,
        ),
    )

    response = scanner.scan(now=datetime(2026, 4, 28, 12, 0))

    assert response.scanned_at.tzinfo is UTC
    assert response.event_count == 0


def test_attention_scan_reports_warning_for_report_job_before_sla(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    batch_ledger = ReportBatchLedger(tmp_path / "batches.sqlite3")
    record = report_ledger.create_portfolio_review_job(
        request=_report_request(),
        caller_context=_caller(),
        idempotency_key="attention-report-warning",
    )
    _set_report_job_updated_at(report_ledger, record.job_id, OLD_AT)

    scanner = ReportingAttentionScanner(
        report_job_ledger=report_ledger,
        batch_ledger=batch_ledger,
        config=AttentionScanConfig(
            report_job_stuck_threshold_seconds=300,
            batch_item_stuck_threshold_seconds=300,
            sla_breach_threshold_seconds=10_000,
        ),
    )

    response = scanner.scan(now=SCAN_AT)

    assert response.event_count == 1
    event = response.events[0]
    assert event.attention_type == "stuck_state"
    assert event.severity == "warning"
    assert event.reason == "report_job_active_state_exceeded_stuck_threshold"
    assert event.threshold_seconds == 300


def test_attention_scan_derives_batch_reasons_and_skips_terminal_items() -> None:
    waiting = _batch_item(
        batch_item_id="rbci_waiting",
        status="waiting_on_report_job",
        dispatched_at=OLD_AT,
        report_job_id="rjob_waiting",
    )
    materialized = _batch_item(
        batch_item_id="rbci_materialized",
        status="materialized",
        created_at=OLD_AT,
    )
    terminal = _batch_item(
        batch_item_id="rbci_succeeded",
        status="succeeded",
        completed_at=OLD_AT,
    )
    batch = _batch([waiting, materialized, terminal])
    scanner = ReportingAttentionScanner(
        report_job_ledger=_FakeReportLedger(),
        batch_ledger=_FakeBatchLedger(batch),
        config=AttentionScanConfig(
            report_job_stuck_threshold_seconds=300,
            batch_item_stuck_threshold_seconds=300,
            sla_breach_threshold_seconds=10_000,
        ),
    )

    response = scanner.scan(now=SCAN_AT)

    assert response.event_count == 2
    reasons = {event.resource_id: event.reason for event in response.events}
    assert reasons["rbci_waiting"] == ("batch_item_waiting_on_report_job_exceeded_stuck_threshold")
    assert reasons["rbci_materialized"] == "batch_item_active_state_exceeded_stuck_threshold"
    assert "rbci_succeeded" not in reasons


def test_attention_scan_reports_batch_sla_breach() -> None:
    batch = _batch(
        [
            _batch_item(
                batch_item_id="rbci_recovery",
                status="recovery_pending",
                started_at=OLD_AT,
            )
        ]
    )
    scanner = ReportingAttentionScanner(
        report_job_ledger=_FakeReportLedger(),
        batch_ledger=_FakeBatchLedger(batch),
        config=AttentionScanConfig(
            report_job_stuck_threshold_seconds=300,
            batch_item_stuck_threshold_seconds=300,
            sla_breach_threshold_seconds=300,
        ),
    )

    response = scanner.scan(now=SCAN_AT)

    assert response.event_count == 1
    event = response.events[0]
    assert event.attention_type == "sla_breach"
    assert event.severity == "critical"
    assert event.reason == "batch_item_active_state_exceeded_sla_threshold"


def _set_report_job_updated_at(
    ledger: ReportJobLedger,
    report_job_id: str,
    updated_at: datetime,
) -> None:
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE report_job SET updated_at = ? WHERE report_job_id = ?",
            (_dt_to_text(updated_at), report_job_id),
        )


class _FakeReportLedger:
    def list_jobs(self, *, filters: ReportJobListFilters):
        return []


class _FakeBatchLedger:
    def __init__(self, batch: ReportBatchRecord):
        self._batch = batch

    def list_attention_batch_ids(self, *, limit: int, now: datetime | None = None):
        return [self._batch.batch_id][:limit]

    def get_batch(self, batch_id: str) -> ReportBatchRecord:
        assert batch_id == self._batch.batch_id
        return self._batch


def _batch_item(
    *,
    batch_item_id: str,
    status: str,
    created_at: datetime = SCAN_AT,
    report_job_id: str | None = None,
    lease_acquired_at: datetime | None = None,
    last_heartbeat_at: datetime | None = None,
    dispatched_at: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> ReportBatchItemRecord:
    return ReportBatchItemRecord(
        batch_item_id=batch_item_id,
        batch_id="rbch_attention",
        item_position=1,
        portfolio_id="PB_SG_GLOBAL_BAL_001",
        item_idempotency_key=f"attention:{batch_item_id}",
        status=status,
        source_system="lotus-core",
        source_object="PortfolioScope",
        created_at=created_at,
        report_job_id=report_job_id,
        lease_acquired_at=lease_acquired_at,
        last_heartbeat_at=last_heartbeat_at,
        dispatched_at=dispatched_at,
        started_at=started_at,
        completed_at=completed_at,
    )


def _batch(items: list[ReportBatchItemRecord]) -> ReportBatchRecord:
    return ReportBatchRecord(
        batch_id="rbch_attention",
        selector_mode="explicit_portfolio_list",
        tenant_id="tenant-sg",
        region="APAC",
        materialized_portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW"]},
        idempotency_key="attention-batch",
        request_hash="sha256:attention",
        status="running",
        item_count=len(items),
        created_at=OLD_AT,
        updated_at=OLD_AT,
        correlation_id="corr-attention",
        trace_id="trace-attention",
        items=items,
    )
