"""The rerender-command availability matrix (the #311 merged-review
finding, closed): the fact of a retained snapshot is SEPARATE from the
availability of an executable rerender command, and no job advertises a
path it does not have."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_render.rerender_service import rerender_eligible


def _job(**overrides) -> ReportJobLedgerRecord:
    payload = {
        "request_id": "rrq_1",
        "job_id": "rjob_1",
        "report_type": "portfolio_review",
        "portfolio_scope": {"portfolio_ids": ["P1"]},
        "requested_output_formats": ["pdf"],
        "as_of_date": "2026-04-22",
        "reporting_currency": "USD",
        "options": {},
        "trigger_type": "user",
        "triggered_by": "advisor-123",
        "caller_application": "lotus-gateway",
        "tenant_id": "tenant-sg",
        "region": "APAC",
        "idempotency_key": "idem-1",
        "request_hash": "hash-1",
        "status": "archived",
        "current_step": "archived",
        "retry_eligible": False,
        "cancel_requested": False,
        "created_at": datetime(2026, 4, 22, tzinfo=UTC),
        "updated_at": datetime(2026, 4, 22, tzinfo=UTC),
        "correlation_id": "corr-1",
        "trace_id": "trace-1",
        "render_job_id": "rdr_rjob_1_pdf",
        "archive_document_id": "doc_1",
    }
    payload.update(overrides)
    return ReportJobLedgerRecord.model_validate(payload)


def test_an_archived_pdf_job_is_rerenderable() -> None:
    assert rerender_eligible(_job()) is True


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        (
            "json_only",
            {
                "requested_output_formats": ["json"],
                "status": "data_ready",
                "render_job_id": None,
                "archive_document_id": None,
            },
        ),
        ("failed", {"status": "failed", "archive_document_id": None}),
        ("unfinished_pdf_rendering", {"status": "rendering", "archive_document_id": None}),
        ("completed_not_archived", {"status": "completed", "archive_document_id": None}),
        ("archived_without_render_identity", {"render_job_id": None}),
        ("archived_without_document_identity", {"archive_document_id": None}),
    ],
)
def test_jobs_without_the_full_archived_pdf_chain_are_not(label, overrides) -> None:
    """JSON-only, failed, and unfinished-PDF jobs must not advertise a
    rerender path they do not have - the SAME predicate gates the operator
    command, so the claim and the command can never disagree."""

    assert rerender_eligible(_job(**overrides)) is False


def test_replay_derived_jobs_earn_eligibility_by_their_own_lifecycle() -> None:
    """Replay inherits the snapshot (and its capability claim) verbatim,
    but the rerender COMMAND is earned by the replay job's own lifecycle -
    a freshly replayed job is not yet rerenderable, and becomes so exactly
    when it archives."""

    replayed = _job(status="rendering", archive_document_id=None)
    assert rerender_eligible(replayed) is False
    archived = _job()
    assert rerender_eligible(archived) is True
