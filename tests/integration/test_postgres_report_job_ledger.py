from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterator, Mapping
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from app.config import settings
from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    InvalidReportJobTransitionError,
    InvalidReportJobWorkTransitionError,
    MissingIdempotencyKeyError,
    ReportJobNotFoundError,
)
from app.reporting_jobs.models import (
    PortfolioReviewJobRequest,
    ReportCallerContext,
)
from app.reporting_jobs.postgres_ledger import (
    PostgresReportJobLedger,
    _date_from_value,
    _dt_from_value,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
from tests.integration.postgres_adapter_ownership import own_postgres_adapter


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL ledger proof")
    return database_url


def _request_and_context(
    unique_suffix: str,
) -> tuple[PortfolioReviewJobRequest, ReportCallerContext]:
    request = PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": [f"PB_SG_GLOBAL_BAL_001_{unique_suffix}"]},
        as_of_date="2026-04-22",
        requested_output_formats=["json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW"], "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40"},
    )
    caller_context = ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id=f"corr-pg-ledger-{unique_suffix}",
        trace_id=f"trace-pg-ledger-{unique_suffix}",
    )
    return request, caller_context


def _ledger() -> PostgresReportJobLedger:
    return own_postgres_adapter(PostgresReportJobLedger(_database_url()))


def test_postgres_report_job_ledger_persists_idempotent_job_and_status_events() -> None:
    ledger = _ledger()
    ledger.check_ready()

    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)

    first = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-{unique_suffix}",
    )
    second = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-{unique_suffix}",
    )

    assert second == first
    assert ledger.get_job(first.job_id).job_id == first.job_id

    cancelled = ledger.cancel_job(
        job_id=first.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-cancel-{unique_suffix}",
        trace_id=f"trace-pg-cancel-{unique_suffix}",
    )

    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested is True
    assert [event.to_status for event in ledger.list_status_events(first.job_id)] == [
        "accepted",
        "cancelled",
    ]


def test_postgres_report_submission_persists_and_recovers_durable_work() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    idempotency_key = f"portfolio-review-pg-work-{unique_suffix}"

    first = ledger.submit_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=idempotency_key,
    )
    second = ledger.submit_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=idempotency_key,
    )
    assert second.job_id == first.job_id
    work_item = ledger.get_work_item_for_job(first.job_id)
    assert work_item is not None
    assert work_item.status == "pending"

    now = datetime.now(UTC) + timedelta(seconds=1)
    claimed = ledger.claim_work_items(
        worker_id="report-worker-1",
        limit=1,
        lease_seconds=30,
        now=now,
    )
    matching = [item for item in claimed if item.report_job_id == first.job_id]
    assert len(matching) == 1
    leased = matching[0]
    assert leased.lease_token is not None

    with pytest.raises(InvalidReportJobWorkTransitionError):
        ledger.complete_work_item(
            work_item_id=leased.work_item_id,
            lease_token="wrong-token",
            now=now,
        )

    retry = ledger.fail_work_item(
        work_item_id=leased.work_item_id,
        lease_token=leased.lease_token or "",
        error_category="worker_failure",
        error_summary="temporary worker failure",
        retry_policy=ReportJobWorkRetryPolicy(max_attempts=2, base_delay_seconds=1),
        now=now,
    )
    assert retry.status == "retry_pending"

    reclaimed = ledger.claim_work_items(
        worker_id="report-worker-2",
        limit=25,
        lease_seconds=30,
        now=now + timedelta(seconds=1),
    )
    recovered = next(item for item in reclaimed if item.work_item_id == leased.work_item_id)
    completed = ledger.complete_work_item(
        work_item_id=recovered.work_item_id,
        lease_token=recovered.lease_token or "",
        now=now + timedelta(seconds=1),
    )
    assert completed.status == "completed"


def test_postgres_report_work_queue_enforces_lease_ownership_and_terminal_retry() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.submit_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-terminal-work-{unique_suffix}",
    )

    assert (
        ledger.claim_work_items(
            worker_id="report-worker-idle",
            limit=0,
            lease_seconds=30,
        )
        == []
    )

    now = datetime.now(UTC) + timedelta(seconds=1)
    leased = next(
        item
        for item in ledger.claim_work_items(
            worker_id="report-worker-terminal",
            limit=25,
            lease_seconds=30,
            now=now,
        )
        if item.report_job_id == job.job_id
    )
    with pytest.raises(
        InvalidReportJobWorkTransitionError,
        match="report_job_work_lease_not_owned",
    ):
        ledger.fail_work_item(
            work_item_id=leased.work_item_id,
            lease_token="wrong-token",
            error_category="worker_failure",
            error_summary="foreign worker must not mutate this lease",
            now=now,
        )

    terminal = ledger.fail_work_item(
        work_item_id=leased.work_item_id,
        lease_token=leased.lease_token or "",
        error_category="worker_failure_category_that_is_intentionally_long_" * 3,
        error_summary="  permanent   worker failure  " * 30,
        retry_policy=ReportJobWorkRetryPolicy(max_attempts=1),
        now=now,
    )
    assert terminal.status == "failed"
    assert terminal.lease_token is None
    assert terminal.last_error_category is not None
    assert len(terminal.last_error_category) == 80
    assert terminal.last_error_summary is not None
    assert len(terminal.last_error_summary) <= 240
    assert "  " not in terminal.last_error_summary
    source_job = ledger.get_job(job.job_id)
    assert source_job.status == "failed"
    assert source_job.failure_category == "operator_intervention_required"
    assert source_job.retry_eligible is True
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "failed",
    ]


def test_postgres_expired_work_lease_stops_at_retry_boundary() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.submit_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-expired-work-{unique_suffix}",
    )
    now = datetime.now(UTC) + timedelta(seconds=1)
    policy = ReportJobWorkRetryPolicy(max_attempts=1, base_delay_seconds=1)
    leased = next(
        item
        for item in ledger.claim_work_items(
            worker_id="report-worker-expiring",
            limit=25,
            lease_seconds=30,
            retry_policy=policy,
            now=now,
        )
        if item.report_job_id == job.job_id
    )

    reclaimed = ledger.claim_work_items(
        worker_id="report-worker-recovery",
        limit=25,
        lease_seconds=30,
        retry_policy=policy,
        now=now + timedelta(seconds=31),
    )

    assert leased.work_item_id not in {item.work_item_id for item in reclaimed}
    exhausted = ledger.get_work_item_for_job(job.job_id)
    assert exhausted is not None
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 1
    assert exhausted.last_error_category == "expired_work_lease"
    source_job = ledger.get_job(job.job_id)
    assert source_job.status == "failed"
    assert source_job.failure_category == "timeout"
    assert source_job.retry_eligible is True
    assert [event.to_status for event in ledger.list_status_events(job.job_id)] == [
        "accepted",
        "failed",
    ]


def test_postgres_report_job_relationship_is_idempotent_and_queryable_from_both_jobs() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    source = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-source-{unique_suffix}",
    )
    derived = ledger.create_portfolio_review_job(
        request=request.model_copy(
            update={"portfolio_scope": {"portfolio_ids": [f"PB_SG_DERIVED_{unique_suffix}"]}}
        ),
        caller_context=caller_context.model_copy(
            update={
                "correlation_id": f"corr-pg-derived-{unique_suffix}",
                "trace_id": f"trace-pg-derived-{unique_suffix}",
            }
        ),
        idempotency_key=f"portfolio-review-pg-derived-{unique_suffix}",
    )

    relationship = ledger.upsert_job_relationship(
        source_job=source,
        derived_job=derived,
        relationship_type="failed_work_replay",
        actor="operations-control",
        reason="Replay after source data recovery.",
        archive_consequence="new_document_version",
        previous_archive_document_id="doc_previous",
        new_archive_document_id="doc_replayed",
    )
    updated = ledger.upsert_job_relationship(
        source_job=source,
        derived_job=derived,
        relationship_type="failed_work_replay",
        actor="operations-supervisor",
        reason="Replay approved after source reconciliation.",
        archive_consequence="new_document_version",
        previous_archive_document_id="doc_previous",
        new_archive_document_id="doc_replayed",
    )

    assert updated.relationship_id == relationship.relationship_id
    assert updated.actor == "operations-supervisor"
    assert updated.reason == "Replay approved after source reconciliation."
    assert ledger.list_job_relationships(source.job_id) == [updated]
    assert ledger.list_job_relationships(derived.job_id) == [updated]


def test_postgres_replay_derived_job_guard_enforces_one_replacement() -> None:
    """The one-replacement guard runs inside the PostgreSQL creation
    transaction: a novel replay key is refused while a live or successful
    replacement exists, the same key still converges idempotently, and a
    failed replacement does not block another attempt."""

    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    source = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"replay-guard-source-{unique_suffix}",
    )
    ledger.mark_failed(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
        failure_category="render_execution_failed",
        failure_message="Render unavailable.",
        retry_eligible=True,
    )

    first = ledger.create_replay_derived_job(
        source_job_id=source.job_id,
        request=request,
        caller_context=caller_context,
        idempotency_key=f"replay-guard-first-{unique_suffix}",
        reason="First replacement.",
    )
    ledger.upsert_job_relationship(
        source_job=source,
        derived_job=first,
        relationship_type="failed_work_replay",
        actor="operations-control",
        reason="First replacement.",
    )

    # A live replacement (accepted) blocks any novel key...
    with pytest.raises(InvalidReportJobTransitionError):
        ledger.create_replay_derived_job(
            source_job_id=source.job_id,
            request=request,
            caller_context=caller_context,
            idempotency_key=f"replay-guard-second-{unique_suffix}",
            reason="Second replacement attempt.",
        )
    # ...while the original key still converges on the existing job.
    same = ledger.create_replay_derived_job(
        source_job_id=source.job_id,
        request=request,
        caller_context=caller_context,
        idempotency_key=f"replay-guard-first-{unique_suffix}",
        reason="First replacement.",
    )
    assert same.job_id == first.job_id

    # The post-lock idempotency recheck: the SAME key converges on the
    # existing replacement even when presented while the guard would refuse a
    # novel key (the concurrent same-key race resolves to convergence, never
    # to a 409).
    converged = ledger.create_replay_derived_job(
        source_job_id=source.job_id,
        request=request,
        caller_context=caller_context,
        idempotency_key=f"replay-guard-first-{unique_suffix}",
        reason="First replacement.",
    )
    assert converged.job_id == first.job_id

    # A FAILED replacement releases the guard for a fresh attempt.
    ledger.mark_failed(
        job_id=first.job_id,
        actor=first.triggered_by,
        correlation_id=first.correlation_id,
        trace_id=first.trace_id,
        failure_category="render_execution_failed",
        failure_message="Replacement render failed too.",
        retry_eligible=True,
    )
    third = ledger.create_replay_derived_job(
        source_job_id=source.job_id,
        request=request,
        caller_context=caller_context,
        idempotency_key=f"replay-guard-third-{unique_suffix}",
        reason="Third replacement after failure.",
    )
    assert third.job_id not in {source.job_id, first.job_id}


def test_postgres_report_job_ledger_marks_collecting_data_data_ready_and_failed() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)

    ready = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-ready-{unique_suffix}",
    )
    failed = ledger.create_portfolio_review_job(
        request=request.model_copy(
            update={"portfolio_scope": {"portfolio_ids": [f"PB_SG_GLOBAL_BAL_002_{unique_suffix}"]}}
        ),
        caller_context=caller_context.model_copy(
            update={
                "correlation_id": f"corr-pg-failed-{unique_suffix}",
                "trace_id": f"trace-pg-failed-{unique_suffix}",
            }
        ),
        idempotency_key=f"portfolio-review-pg-failed-{unique_suffix}",
    )

    collecting = ledger.mark_collecting_data(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-collect-{unique_suffix}",
        trace_id=f"trace-pg-collect-{unique_suffix}",
    )
    assert collecting.status == "collecting_data"
    assert collecting.started_at is not None

    data_ready = ledger.mark_data_ready(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-data-ready-{unique_suffix}",
        trace_id=f"trace-pg-data-ready-{unique_suffix}",
    )
    assert data_ready.status == "data_ready"
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "collecting_data",
        "data_ready",
    ]

    failed_record = ledger.mark_failed(
        job_id=failed.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-mark-failed-{unique_suffix}",
        trace_id=f"trace-pg-mark-failed-{unique_suffix}",
        failure_category="validation_failed",
        failure_message="Requested report inputs were not fully supported.",
        retry_eligible=False,
    )
    assert failed_record.status == "failed"
    assert failed_record.failure_category == "validation_failed"
    assert failed_record.retry_eligible is False
    assert [event.to_status for event in ledger.list_status_events(failed.job_id)] == [
        "accepted",
        "failed",
    ]


def test_postgres_report_job_ledger_persists_render_and_archive_handoff() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.create_portfolio_review_job(
        request=request.model_copy(update={"requested_output_formats": ["pdf"]}),
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-archive-{unique_suffix}",
    )

    ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-archive-ready-{unique_suffix}",
        trace_id=f"trace-pg-archive-ready-{unique_suffix}",
    )
    rendering = ledger.mark_rendering(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-archive-rendering-{unique_suffix}",
        trace_id=f"trace-pg-archive-rendering-{unique_suffix}",
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
    )
    assert rendering.status == "rendering"

    completed = ledger.mark_completed(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-archive-complete-{unique_suffix}",
        trace_id=f"trace-pg-archive-complete-{unique_suffix}",
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
        artifact_sha256="sha256:artifact",
        bounded_determinism_fingerprint="fingerprint",
        runtime_engine="typst",
        runtime_engine_version="0.14.2",
        render_duration_ms=812,
    )
    assert completed.status == "completed"

    archiving = ledger.mark_archiving(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-archive-start-{unique_suffix}",
        trace_id=f"trace-pg-archive-start-{unique_suffix}",
        archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
    )
    assert archiving.status == "archiving"
    assert archiving.archive_request_id == f"arch_rdr_{ready.job_id}_pdf"

    archived = ledger.mark_archived(
        job_id=ready.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-archive-end-{unique_suffix}",
        trace_id=f"trace-pg-archive-end-{unique_suffix}",
        archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
        archive_document_id=f"doc_{unique_suffix}",
    )
    assert archived.status == "archived"
    assert archived.archive_document_id == f"doc_{unique_suffix}"
    assert archived.archive_completed_at is not None
    archive_statuses = ledger.get_archive_statuses_by_job_ids(
        [ready.job_id, f"rjob_missing_{unique_suffix}", ready.job_id],
        tenant_id="tenant-sg",
    )
    assert [status.report_job_id for status in archive_statuses] == [ready.job_id]
    assert archive_statuses[0].status == "archived"
    assert archive_statuses[0].archive_document_id == f"doc_{unique_suffix}"
    # The persistence boundary itself must withhold the row from another tenant, so neither
    # the lifecycle status nor the archive_document_id can reach a foreign projection.
    assert ledger.get_archive_statuses_by_job_ids([ready.job_id], tenant_id="tenant-uk") == []
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
        "archiving",
        "archived",
    ]


def test_postgres_report_job_ledger_transition_helper_branches() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-transition-{unique_suffix}",
    )

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.mark_collecting_data(
            job_id=f"rjob_missing_{unique_suffix}",
            actor="advisor-123",
            correlation_id=f"corr-pg-missing-{unique_suffix}",
            trace_id=f"trace-pg-missing-{unique_suffix}",
        )

    data_ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-first-ready-{unique_suffix}",
        trace_id=f"trace-pg-first-ready-{unique_suffix}",
    )
    same_status = ledger.mark_data_ready(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id=f"corr-pg-second-ready-{unique_suffix}",
        trace_id=f"trace-pg-second-ready-{unique_suffix}",
    )
    assert same_status == data_ready

    with pytest.raises(
        InvalidReportJobTransitionError,
        match="report_job_invalid_transition",
    ):
        ledger.mark_collecting_data(
            job_id=job.job_id,
            actor="advisor-123",
            correlation_id=f"corr-pg-invalid-{unique_suffix}",
            trace_id=f"trace-pg-invalid-{unique_suffix}",
        )


def test_postgres_report_job_ledger_rejects_missing_idempotency_key() -> None:
    request, caller_context = _request_and_context(uuid4().hex)

    with pytest.raises(MissingIdempotencyKeyError, match="missing_idempotency_key"):
        _ledger().create_portfolio_review_job(
            request=request,
            caller_context=caller_context,
            idempotency_key=" ",
        )


def test_postgres_report_job_ledger_rejects_idempotency_key_reuse_for_different_request() -> None:
    ledger = _ledger()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    idempotency_key = f"portfolio-review-pg-conflict-{unique_suffix}"

    ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=idempotency_key,
    )

    conflicting_request = request.model_copy(
        update={"portfolio_scope": {"portfolio_ids": [f"PB_SG_ALT_BAL_001_{unique_suffix}"]}}
    )

    with pytest.raises(
        IdempotencyConflictError,
        match="idempotency_key_reused_with_different_request",
    ):
        ledger.create_portfolio_review_job(
            request=conflicting_request,
            caller_context=caller_context,
            idempotency_key=idempotency_key,
        )


def test_postgres_report_job_ledger_handles_missing_and_terminal_job_transitions() -> None:
    ledger = _ledger()
    unknown_job_id = f"rjob_missing_{uuid4().hex}"

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.get_job(unknown_job_id)

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.cancel_job(
            job_id=unknown_job_id,
            actor="advisor-123",
            correlation_id="corr-missing-job",
            trace_id="trace-missing-job",
        )

    assert ledger.list_status_events(unknown_job_id) == []

    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-terminal-{unique_suffix}",
    )
    ledger.cancel_job(
        job_id=job.job_id,
        actor="advisor-123",
        correlation_id="corr-terminal-job",
        trace_id="trace-terminal-job",
    )

    with pytest.raises(
        InvalidReportJobTransitionError,
        match="report_job_cannot_be_cancelled",
    ):
        ledger.cancel_job(
            job_id=job.job_id,
            actor="advisor-123",
            correlation_id="corr-terminal-job-2",
            trace_id="trace-terminal-job-2",
        )


def test_postgres_report_job_ledger_check_ready_reports_missing_schema() -> None:
    ledger = object.__new__(PostgresReportJobLedger)

    class _Cursor:
        def fetchall(self) -> list[Mapping[str, Any]]:
            return [{"table_name": "report_request"}]

    class _Connection:
        def execute(self, *_args: object, **_kwargs: object) -> _Cursor:
            return _Cursor()

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield _Connection()

    ledger._connect = _connect  # type: ignore[method-assign]

    with pytest.raises(
        RuntimeError,
        match=(
            "report_job_ledger_schema_missing:report_job,report_job_work_item,report_status_event"
        ),
    ):
        ledger.check_ready()


def test_postgres_report_job_ledger_check_ready_reports_missing_archive_schema() -> None:
    ledger = object.__new__(PostgresReportJobLedger)

    class _Cursor:
        def __init__(self, rows: list[Mapping[str, Any]]):
            self._rows = rows

        def fetchall(self) -> list[Mapping[str, Any]]:
            return self._rows

    class _Connection:
        def execute(self, query: str, *_args: object, **_kwargs: object) -> _Cursor:
            if "table_name = 'report_job_relationship'" in query:
                return _Cursor([{"table_name": "report_job_relationship"}])
            if "information_schema.tables" in query:
                return _Cursor(
                    [
                        {"table_name": "report_request"},
                        {"table_name": "report_job"},
                        {"table_name": "report_status_event"},
                        {"table_name": "report_job_work_item"},
                    ]
                )
            if "table_name = 'report_status_event'" in query:
                return _Cursor(
                    [
                        {"column_name": "event_schema_version"},
                        {"column_name": "event_family"},
                        {"column_name": "event_payload_json"},
                        {"column_name": "event_idempotency_key"},
                    ]
                )
            return _Cursor([{"column_name": "archive_request_id"}])

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield _Connection()

    ledger._connect = _connect  # type: ignore[method-assign]

    with pytest.raises(
        RuntimeError,
        match="report_job_ledger_archive_schema_missing:archive_completed_at,archive_document_id",
    ):
        ledger.check_ready()


def test_postgres_report_job_ledger_load_by_request_id_requires_existing_row() -> None:
    ledger = _ledger()

    with ledger._connect() as connection:
        with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
            ledger._load_by_request_id(connection, f"rrq_missing_{uuid4().hex}")


def test_postgres_report_job_ledger_rolls_back_failed_connection_scope() -> None:
    ledger = _ledger()

    with pytest.raises(RuntimeError, match="force rollback path"):
        with ledger._connect() as _connection:
            raise RuntimeError("force rollback path")


def test_postgres_report_job_ledger_translates_unique_violation_race_without_existing_row() -> None:
    ledger = object.__new__(PostgresReportJobLedger)
    request, caller_context = _request_and_context(uuid4().hex)

    class _InitialConnection:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise UniqueViolation("duplicate idempotency key")

    class _MissingExistingCursor:
        def fetchone(self) -> None:
            return None

    class _RecoveryConnection:
        def execute(self, *_args: object, **_kwargs: object) -> _MissingExistingCursor:
            return _MissingExistingCursor()

    connections: list[object] = [_InitialConnection(), _RecoveryConnection()]

    @contextmanager
    def _connect() -> Iterator[object]:
        yield connections.pop(0)

    ledger._connect = _connect  # type: ignore[method-assign]

    with pytest.raises(IdempotencyConflictError, match="idempotency_key_unique_violation"):
        ledger.create_portfolio_review_job(
            request=request,
            caller_context=caller_context,
            idempotency_key=f"portfolio-review-pg-race-{uuid4().hex}",
        )


def test_postgres_report_job_ledger_recovers_unique_violation_when_existing_row_is_visible() -> (
    None
):
    ledger = object.__new__(PostgresReportJobLedger)
    request, caller_context = _request_and_context(uuid4().hex)
    existing = {
        "report_request_id": "rrq_existing_visible",
        "request_hash": "existing-hash",
    }

    class _InitialConnection:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise UniqueViolation("duplicate idempotency key")

    class _ExistingCursor:
        def fetchone(self) -> Mapping[str, str]:
            return existing

    class _RecoveryConnection:
        def execute(self, *_args: object, **_kwargs: object) -> _ExistingCursor:
            return _ExistingCursor()

    connections: list[object] = [_InitialConnection(), _RecoveryConnection()]

    @contextmanager
    def _connect() -> Iterator[object]:
        yield connections.pop(0)

    def _existing_or_conflict(
        _connection: object,
        visible_existing: Mapping[str, Any],
        _request_hash: str,
    ) -> str:
        assert visible_existing == existing
        return "recovered-from-visible-existing-row"

    ledger._connect = _connect  # type: ignore[method-assign]
    ledger._existing_or_conflict = _existing_or_conflict  # type: ignore[method-assign]

    recovered = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"portfolio-review-pg-visible-race-{uuid4().hex}",
    )

    assert recovered == "recovered-from-visible-existing-row"


def test_postgres_report_job_ledger_cancel_detects_job_deleted_after_lock() -> None:
    ledger = object.__new__(PostgresReportJobLedger)

    class _StatusCursor:
        def fetchone(self) -> Mapping[str, str]:
            return {"status": "accepted"}

    class _MissingRowCursor:
        def fetchone(self) -> None:
            return None

    class _Connection:
        def __init__(self) -> None:
            self.statement_count = 0

        def execute(self, *_args: object, **_kwargs: object) -> _StatusCursor | _MissingRowCursor:
            self.statement_count += 1
            if self.statement_count == 1:
                return _StatusCursor()
            return _MissingRowCursor()

    @contextmanager
    def _connect() -> Iterator[_Connection]:
        yield _Connection()

    ledger._connect = _connect  # type: ignore[method-assign]

    with pytest.raises(ReportJobNotFoundError, match="report_job_not_found"):
        ledger.cancel_job(
            job_id=f"rjob_deleted_after_lock_{uuid4().hex}",
            actor="advisor-123",
            correlation_id="corr-deleted-after-lock",
            trace_id="trace-deleted-after-lock",
        )


def test_postgres_report_job_ledger_value_parsers_accept_driver_and_text_values() -> None:
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)

    assert _dt_from_value(None) is None
    assert _dt_from_value(now) == now
    assert _dt_from_value("2026-04-23T12:00:00Z") == now
    assert _date_from_value(date(2026, 4, 23)) == date(2026, 4, 23)
    assert _date_from_value("2026-04-23") == date(2026, 4, 23)


def test_report_job_ledger_service_returns_postgres_ledger() -> None:
    settings.report_job_ledger_database_url = _database_url()
    get_report_job_ledger.cache_clear()
    try:
        ledger = get_report_job_ledger()
        ledger.check_ready()
        assert isinstance(ledger, PostgresReportJobLedger)
    finally:
        get_report_job_ledger.cache_clear()


def test_isolated_session_rebinds_every_configuration_surface():
    """Issue #179 review: patching os.environ alone left the cached settings object
    and the lru-cached connection provider on the product DSN. All three surfaces
    must agree on the session's helper-owned database."""

    if os.environ.get("REPORT_JOB_LEDGER_DATABASE_IS_ISOLATED"):
        pytest.skip("caller owns the database; the session did not provision one")
    source = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not source:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for the isolation proof")

    from app.config import settings

    assert "_ci_" in source, "the session fixture must have swapped the environment URL"
    assert settings.report_job_ledger_database_url == source, (
        "the cached settings object must carry the session database, not the import-time DSN"
    )


def _pg_failed_archive_attempt(ledger, job, *, unique_suffix: str, index: int):
    attempt, created = ledger.create_rerender_attempt(
        job=job,
        snapshot_id="rsnap_pg_scan",
        snapshot_hash="sha256:snapshot-pg-scan",
        idempotency_key=f"rerender-pg-{unique_suffix}-{index:03d}",
        actor="advisor-123",
        reason="Template correction.",
        correlation_id=f"corr-rerender-pg-{unique_suffix}-{index:03d}",
        trace_id=f"trace-rerender-pg-{unique_suffix}-{index:03d}",
    )
    assert created is True
    return ledger.mark_rerender_failed(
        rerender_attempt_id=attempt.rerender_attempt_id,
        actor="advisor-123",
        correlation_id=f"corr-rerender-pg-{unique_suffix}-{index:03d}",
        trace_id=f"trace-rerender-pg-{unique_suffix}-{index:03d}",
        failure_category="archive_storage_failed",
        failure_message="Archive response lost.",
        retry_eligible=True,
    )


def test_postgres_rerender_ambiguity_scan_adoption_and_failure_clearing() -> None:
    """Issue #215 (PR #219 review), PostgreSQL mirror of the sqlite pins:
    the ambiguity scan is unlimited and newest-first, adoption outcomes bind
    the incoming idempotency key and converge on same-key retries, and
    resolving an attempt to archived clears its failure posture."""

    ledger = _ledger()
    ledger.check_ready()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"rerender-pg-scan-{unique_suffix}",
    )

    failed = [
        _pg_failed_archive_attempt(ledger, job, unique_suffix=unique_suffix, index=i)
        for i in range(30)
    ]

    scanned = ledger.list_unresolved_archive_ambiguous_attempts(job.job_id)
    assert len(scanned) == 30
    assert len(scanned) > len(ledger.list_rerender_attempts(job.job_id))
    assert {attempt.rerender_attempt_id for attempt in scanned} == {
        attempt.rerender_attempt_id for attempt in failed
    }
    timestamps = [(attempt.updated_at, attempt.created_at) for attempt in scanned]
    assert timestamps == sorted(timestamps, reverse=True)

    ambiguous = scanned[0]
    outcome = ledger.record_adopted_rerender_outcome(
        job=job,
        idempotency_key=f"rerender-pg-adopt-{unique_suffix}",
        actor="advisor-123",
        reason="Retry the correction.",
        correlation_id=f"corr-rerender-pg-adopt-{unique_suffix}",
        trace_id=f"trace-rerender-pg-adopt-{unique_suffix}",
        adopted_attempt=ambiguous,
        archive_document_id="doc_pg_committed",
    )
    assert outcome.rerender_attempt_id != ambiguous.rerender_attempt_id
    assert outcome.status == "archived"
    assert outcome.render_job_id == ambiguous.render_job_id
    assert outcome.archive_request_id == f"arch_{ambiguous.render_job_id}"
    assert outcome.archive_document_id == "doc_pg_committed"
    assert outcome.retry_eligible is False

    repeat = ledger.record_adopted_rerender_outcome(
        job=job,
        idempotency_key=f"rerender-pg-adopt-{unique_suffix}",
        actor="advisor-123",
        reason="Retry the correction.",
        correlation_id=f"corr-rerender-pg-adopt2-{unique_suffix}",
        trace_id=f"trace-rerender-pg-adopt2-{unique_suffix}",
        adopted_attempt=ambiguous,
        archive_document_id="doc_pg_committed",
    )
    assert repeat.rerender_attempt_id == outcome.rerender_attempt_id
    via_create, created = ledger.create_rerender_attempt(
        job=job,
        snapshot_id=ambiguous.snapshot_id,
        snapshot_hash=ambiguous.snapshot_hash,
        idempotency_key=f"rerender-pg-adopt-{unique_suffix}",
        actor="advisor-123",
        reason="Retry the correction.",
        correlation_id=f"corr-rerender-pg-adopt3-{unique_suffix}",
        trace_id=f"trace-rerender-pg-adopt3-{unique_suffix}",
    )
    assert created is False
    assert via_create.rerender_attempt_id == outcome.rerender_attempt_id

    archived = ledger.mark_rerender_archived(
        rerender_attempt_id=ambiguous.rerender_attempt_id,
        actor="advisor-123",
        correlation_id=f"corr-rerender-pg-clear-{unique_suffix}",
        trace_id=f"trace-rerender-pg-clear-{unique_suffix}",
        archive_document_id="doc_pg_committed",
    )
    assert archived.status == "archived"
    assert archived.failure_category is None
    assert archived.failure_message is None
    assert archived.retry_eligible is False
    assert len(ledger.list_unresolved_archive_ambiguous_attempts(job.job_id)) == 29


def test_postgres_rerender_adoption_rejects_missing_idempotency_key() -> None:
    ledger = _ledger()
    ledger.check_ready()
    unique_suffix = uuid4().hex
    request, caller_context = _request_and_context(unique_suffix)
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller_context,
        idempotency_key=f"rerender-pg-guard-{unique_suffix}",
    )
    ambiguous = _pg_failed_archive_attempt(ledger, job, unique_suffix=unique_suffix, index=0)

    with pytest.raises(MissingIdempotencyKeyError):
        ledger.record_adopted_rerender_outcome(
            job=job,
            idempotency_key="   ",
            actor="advisor-123",
            reason="Retry the correction.",
            correlation_id=f"corr-rerender-pg-guard-{unique_suffix}",
            trace_id=f"trace-rerender-pg-guard-{unique_suffix}",
            adopted_attempt=ambiguous,
            archive_document_id="doc_pg_guard",
        )


def test_postgres_bounded_relationship_reason_normalizes_blank() -> None:
    from app.reporting_jobs.postgres_ledger import _bounded_relationship_reason

    assert _bounded_relationship_reason("   ") == "not_provided"
    assert _bounded_relationship_reason("b" * 300) == "b" * 240
