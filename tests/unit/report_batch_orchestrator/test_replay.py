from datetime import UTC, datetime

import pytest

from app.report_batch_orchestrator.models import (
    BatchItemReplayRequest,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.report_batch_orchestrator.replay import (
    ReportBatchItemReplayService,
    _batch_item_replay_idempotency_key,
    get_report_batch_item_replay_service,
)
from app.reporting_jobs.ledger import (
    InvalidReportJobTransitionError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
)
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext


def _now() -> datetime:
    return datetime(2026, 4, 22, tzinfo=UTC)


def _caller() -> ReportCallerContext:
    return ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id="corr-batch-replay",
        trace_id="trace-batch-replay",
    )


def _batch(item: ReportBatchItemRecord) -> ReportBatchRecord:
    return ReportBatchRecord(
        batch_id=item.batch_id,
        selector_mode="explicit_portfolio_list",
        tenant_id="tenant-sg",
        region="APAC",
        materialized_portfolio_ids=[item.portfolio_id],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
        idempotency_key="batch-key",
        request_hash="hash",
        status="running",
        item_count=1,
        created_at=_now(),
        correlation_id="corr-batch-replay",
        trace_id="trace-batch-replay",
        items=[item],
    )


def _item(**overrides) -> ReportBatchItemRecord:
    payload = {
        "batch_item_id": "rbit_replay",
        "batch_id": "rbch_replay",
        "item_position": 1,
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "item_idempotency_key": "item-key",
        "status": "failed_retryable",
        "source_system": "lotus-core",
        "source_object": "PortfolioScope",
        "created_at": _now(),
        "attempt_count": 1,
        "retry_eligible": True,
    }
    payload.update(overrides)
    return ReportBatchItemRecord(**payload)


def _source_job(ledger: ReportJobLedger):
    job = ledger.create_portfolio_review_job(
        request=PortfolioReviewJobRequest(
            portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
            as_of_date="2026-04-22",
            requested_output_formats=["pdf"],
            reporting_currency="USD",
            options={"sections": ["OVERVIEW", "PERFORMANCE"]},
        ),
        caller_context=_caller(),
        idempotency_key="source-batch-job",
    )
    return ledger.mark_failed(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
        failure_category="upstream_data_failed",
        failure_message="Upstream timeout.",
        retry_eligible=True,
    )


class _ReplayLedger:
    def __init__(self, item: ReportBatchItemRecord) -> None:
        self.item = item
        self.relinks = 0

    def get_batch(self, batch_id: str) -> ReportBatchRecord:
        return _batch(self.item)

    def get_batch_item(self, batch_id: str, batch_item_id: str) -> ReportBatchItemRecord:
        return self.item

    def relink_failed_item_for_replay(
        self,
        *,
        batch_id: str,
        batch_item_id: str,
        replayed_report_job_id: str,
        retry_policy=None,
    ) -> ReportBatchItemRecord:
        self.relinks += 1
        self.item = self.item.model_copy(
            update={
                "status": "waiting_on_report_job",
                "report_job_id": replayed_report_job_id,
                "retry_eligible": False,
            }
        )
        return self.item


def test_batch_replay_same_key_returns_existing_relink(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _source_job(report_ledger)
    replay_key = _batch_item_replay_idempotency_key(
        batch_item_id="rbit_replay",
        idempotency_key="same-key",
    )
    replayed = report_ledger.create_portfolio_review_job(
        request=PortfolioReviewJobRequest(
            portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
            as_of_date="2026-04-22",
            requested_output_formats=["pdf"],
            reporting_currency="USD",
            options={"sections": ["OVERVIEW", "PERFORMANCE"]},
        ),
        caller_context=_caller(),
        idempotency_key=replay_key,
    )
    report_ledger.append_job_event(
        job_id=replayed.job_id,
        event_type="batch_item_replay_lineage_bound",
        message=f"Batch item replay source job {source.job_id}.",
        actor="advisor-123",
        correlation_id="corr-batch-replay",
        trace_id="trace-batch-replay",
    )
    batch_ledger = _ReplayLedger(
        _item(status="waiting_on_report_job", report_job_id=replayed.job_id, retry_eligible=False)
    )
    service = ReportBatchItemReplayService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_ledger,
    )

    result = service.replay_item(
        batch_id="rbch_replay",
        batch_item_id="rbit_replay",
        command=BatchItemReplayRequest(reason="Same command retry."),
        caller_context=_caller(),
        idempotency_key="same-key",
    )

    assert result.source_report_job.job_id == source.job_id
    assert result.replayed_report_job.job_id == replayed.job_id
    assert batch_ledger.relinks == 0


def test_batch_replay_rejects_missing_lineage_and_missing_key(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    replayed = report_ledger.create_portfolio_review_job(
        request=PortfolioReviewJobRequest(
            portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
            as_of_date="2026-04-22",
            requested_output_formats=["pdf"],
            reporting_currency="USD",
            options={"sections": ["OVERVIEW", "PERFORMANCE"]},
        ),
        caller_context=_caller(),
        idempotency_key=_batch_item_replay_idempotency_key(
            batch_item_id="rbit_replay",
            idempotency_key="same-key",
        ),
    )
    service = ReportBatchItemReplayService(
        batch_ledger=_ReplayLedger(
            _item(
                status="waiting_on_report_job",
                report_job_id=replayed.job_id,
                retry_eligible=False,
            )
        ),
        report_job_ledger=report_ledger,
    )

    with pytest.raises(MissingIdempotencyKeyError):
        _batch_item_replay_idempotency_key(batch_item_id="rbit_replay", idempotency_key="")
    with pytest.raises(InvalidReportJobTransitionError):
        service.replay_item(
            batch_id="rbch_replay",
            batch_item_id="rbit_replay",
            command=BatchItemReplayRequest(reason="Missing source lineage."),
            caller_context=_caller(),
            idempotency_key="same-key",
        )


def test_batch_replay_rejects_failed_item_without_source_job(tmp_path):
    service = ReportBatchItemReplayService(
        batch_ledger=_ReplayLedger(_item(report_job_id=None)),
        report_job_ledger=ReportJobLedger(tmp_path / "jobs.sqlite3"),
    )

    with pytest.raises(InvalidReportJobTransitionError):
        service.replay_item(
            batch_id="rbch_replay",
            batch_item_id="rbit_replay",
            command=BatchItemReplayRequest(reason="No linked source job."),
            caller_context=_caller(),
            idempotency_key="missing-source",
        )


def test_batch_replay_service_factory_wires_runtime_dependencies(monkeypatch):
    batch_ledger = object()
    report_job_ledger = object()

    monkeypatch.setattr(
        "app.report_batch_orchestrator.replay.get_report_batch_ledger",
        lambda: batch_ledger,
    )
    monkeypatch.setattr(
        "app.report_batch_orchestrator.replay.get_report_job_ledger",
        lambda: report_job_ledger,
    )

    service = get_report_batch_item_replay_service()

    assert service._batch_ledger is batch_ledger
    assert service._report_job_ledger is report_job_ledger
