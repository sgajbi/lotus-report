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


def test_batch_replay_records_source_derived_relationship(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _source_job(report_ledger)
    batch_ledger = _ReplayLedger(_item(report_job_id=source.job_id))
    service = ReportBatchItemReplayService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_ledger,
    )

    result = service.replay_item(
        batch_id="rbch_replay",
        batch_item_id="rbit_replay",
        command=BatchItemReplayRequest(reason="Retry batch item after upstream recovery."),
        caller_context=_caller(),
        idempotency_key="relationship-key",
    )

    relationships = report_ledger.list_job_relationships(source.job_id)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "batch_item_replay"
    assert relationships[0].source_report_job_id == source.job_id
    assert relationships[0].derived_report_job_id == result.replayed_report_job.job_id
    assert relationships[0].source_status == "failed"
    assert relationships[0].source_failure_category == "upstream_data_failed"
    assert relationships[0].derived_status == "accepted"
    assert report_ledger.list_job_relationships(result.replayed_report_job.job_id) == (
        relationships
    )


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
        event_payload={"source_job_id": source.job_id},
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
    lineage_event = report_ledger.list_status_events(replayed.job_id)[-1]
    assert lineage_event.event_schema_version == "report-status-event.v1"
    assert lineage_event.event_family == "batch_item_replay"
    assert lineage_event.event_payload["source_job_id"] == source.job_id
    assert "source job" in (lineage_event.message or "")


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


def _other_tenant_caller() -> ReportCallerContext:
    return _caller().model_copy(update={"tenant_id": "tenant-uk"})


class _ItemLookupMustNotRun(_ReplayLedger):
    def get_batch_item(self, batch_id: str, batch_item_id: str) -> ReportBatchItemRecord:
        raise AssertionError("Cross-tenant replay must stop before the batch-item lookup.")


def test_batch_replay_rejects_cross_tenant_callers_before_any_item_lookup(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _source_job(report_ledger)
    batch_ledger = _ItemLookupMustNotRun(_item(report_job_id=source.job_id))
    service = ReportBatchItemReplayService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_ledger,
    )

    with pytest.raises(ValueError) as excinfo:
        service.replay_item(
            batch_id="rbch_replay",
            batch_item_id="rbit_replay",
            command=BatchItemReplayRequest(reason="Cross-tenant replay attempt."),
            caller_context=_other_tenant_caller(),
            idempotency_key="cross-tenant-replay",
        )

    assert str(excinfo.value) == "report_batch_not_found"
    assert batch_ledger.relinks == 0


def test_batch_replay_does_not_create_a_report_job_for_a_cross_tenant_caller(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _source_job(report_ledger)
    batch_ledger = _ReplayLedger(_item(report_job_id=source.job_id))
    service = ReportBatchItemReplayService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_ledger,
    )
    events_before = report_ledger.list_status_events(source.job_id)

    with pytest.raises(ValueError):
        service.replay_item(
            batch_id="rbch_replay",
            batch_item_id="rbit_replay",
            command=BatchItemReplayRequest(reason="Cross-tenant replay attempt."),
            caller_context=_other_tenant_caller(),
            idempotency_key="cross-tenant-replay",
        )

    assert batch_ledger.item.status == "failed_retryable"
    assert batch_ledger.item.report_job_id == source.job_id
    assert batch_ledger.relinks == 0
    assert report_ledger.list_status_events(source.job_id) == events_before
    assert report_ledger.list_job_relationships(source.job_id) == []


class _ReplayLedgerRecordingQuarantine(_ReplayLedger):
    def __init__(self, item: ReportBatchItemRecord) -> None:
        super().__init__(item)
        self.quarantines: list[dict[str, object]] = []

    def mark_item_failed(
        self,
        *,
        batch_item_id: str,
        error_category: str,
        error_summary: str,
        retryable: bool,
        retry_policy=None,
    ) -> ReportBatchItemRecord:
        self.quarantines.append(
            {
                "batch_item_id": batch_item_id,
                "error_category": error_category,
                "retryable": retryable,
            }
        )
        self.item = self.item.model_copy(
            update={"status": "failed_terminal", "retry_eligible": False}
        )
        return self.item


class _ForeignTenantJobLedger:
    """Wraps a real job ledger so the linked job reports a different tenant."""

    def __init__(self, inner: ReportJobLedger, foreign_tenant_id: str) -> None:
        self._inner = inner
        self._foreign_tenant_id = foreign_tenant_id
        self.created_jobs = 0

    def get_job(self, job_id: str):
        return self._inner.get_job(job_id).model_copy(update={"tenant_id": self._foreign_tenant_id})

    def create_portfolio_review_job(self, **kwargs):
        self.created_jobs += 1
        raise AssertionError("A foreign-tenant linked job must never be replayed.")

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_replay_refuses_an_item_whose_linked_job_belongs_to_another_tenant(tmp_path):
    """Admitting the batch is not enough: the link can point at another tenant's job."""

    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _source_job(report_ledger)
    batch_ledger = _ReplayLedgerRecordingQuarantine(_item(report_job_id=source.job_id))
    service = ReportBatchItemReplayService(
        batch_ledger=batch_ledger,
        report_job_ledger=_ForeignTenantJobLedger(report_ledger, "tenant-uk"),
    )

    with pytest.raises(InvalidReportJobTransitionError) as excinfo:
        service.replay_item(
            batch_id="rbch_replay",
            batch_item_id="rbit_replay",
            command=BatchItemReplayRequest(reason="Same-tenant caller, foreign linked job."),
            caller_context=_caller(),
            idempotency_key="foreign-linked-job-replay",
        )

    assert str(excinfo.value) == "report_batch_item_cannot_be_replayed"
    assert batch_ledger.relinks == 0
    assert report_ledger.list_job_relationships(source.job_id) == []


def test_replay_quarantines_the_item_so_it_stops_presenting_as_replayable(tmp_path):
    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _source_job(report_ledger)
    batch_ledger = _ReplayLedgerRecordingQuarantine(_item(report_job_id=source.job_id))
    service = ReportBatchItemReplayService(
        batch_ledger=batch_ledger,
        report_job_ledger=_ForeignTenantJobLedger(report_ledger, "tenant-uk"),
    )

    with pytest.raises(InvalidReportJobTransitionError):
        service.replay_item(
            batch_id="rbch_replay",
            batch_item_id="rbit_replay",
            command=BatchItemReplayRequest(reason="Same-tenant caller, foreign linked job."),
            caller_context=_caller(),
            idempotency_key="foreign-linked-job-replay",
        )

    assert batch_ledger.quarantines == [
        {
            "batch_item_id": "rbit_replay",
            "error_category": "batch_item_tenant_mismatch",
            "retryable": False,
        }
    ]
    assert batch_ledger.item.status == "failed_terminal"
    assert batch_ledger.item.retry_eligible is False


def test_replay_still_works_when_the_linked_job_tenant_matches(tmp_path):
    """The comparison must be a no-op on the ordinary path."""

    report_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = _source_job(report_ledger)
    batch_ledger = _ReplayLedgerRecordingQuarantine(_item(report_job_id=source.job_id))
    service = ReportBatchItemReplayService(
        batch_ledger=batch_ledger,
        report_job_ledger=report_ledger,
    )

    result = service.replay_item(
        batch_id="rbch_replay",
        batch_item_id="rbit_replay",
        command=BatchItemReplayRequest(reason="Ordinary same-tenant replay."),
        caller_context=_caller(),
        idempotency_key="same-tenant-replay",
    )

    assert batch_ledger.quarantines == []
    assert result.replayed_report_job.job_id != source.job_id
