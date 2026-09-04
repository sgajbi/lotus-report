"""Template selection is an immutable job fact (steering 2026-09-04).

One authority (REPORT_FAMILY_DEFINITIONS) resolves a report type to its
governed template id/version; the pair is persisted at acceptance for
PDF-capable jobs; the envelope never invents a version; a deployment cannot
change the presentation contract of an already-accepted job; and Render's
response must state the ordered pair or the outcome fails closed.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_service import (
    _caller,
    _job_request,
    _seed_data_ready_job,
)

from app.report_ordering_catalogue import REPORT_FAMILY_DEFINITIONS, resolve_report_template
from app.reporting_jobs import ledger as ledger_module
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_render.document_reference import mint_document_reference
from app.reporting_render.package_builder import (
    _build_render_package,
    template_contract_mismatch,
)


def test_the_resolver_answers_for_every_family_and_refuses_strangers() -> None:
    for definition in REPORT_FAMILY_DEFINITIONS:
        assert resolve_report_template(definition.report_type) == (
            definition.template_id,
            definition.template_version,
        )
    with pytest.raises(LookupError, match="REPORT_TEMPLATE_UNRESOLVED"):
        resolve_report_template("mystery_report")


def test_acceptance_stamps_the_governed_template_on_pdf_jobs(tmp_path) -> None:
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-template",
    )

    assert job.render_template_id == "portfolio-review"
    assert job.render_template_version == "v1"


def test_a_json_only_job_carries_no_template_identity(tmp_path) -> None:
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(requested_output_formats=["json"]),
        caller_context=_caller(),
        idempotency_key="idem-json",
    )

    assert job.render_template_id is None
    assert job.render_template_version is None


def test_a_deployment_cannot_change_an_accepted_jobs_template(tmp_path, monkeypatch) -> None:
    """The core invariant: a job accepted under v1 stays v1 after the family
    default moves to v2. The package is built from the persisted acceptance
    fact, not from whatever the definitions say at render time."""

    _ledger, store, ready = _seed_data_ready_job(tmp_path)
    monkeypatch.setattr(
        ledger_module,
        "accepted_template_identity",
        lambda report_type, output_formats: ("portfolio-review", "v2"),
    )
    snapshot = store.get_snapshot_by_job(ready.job_id)

    package = _build_render_package(
        job=_ledger.get_job(ready.job_id),
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_immutable",
        snapshot_id=snapshot.snapshot_id,
    )

    assert package["template_version"] == "v1"
    assert package["render_context"]["document_reference"] == mint_document_reference(
        report_job_id=ready.job_id,
        snapshot_id=snapshot.snapshot_id,
        template_id="portfolio-review",
        template_version="v1",
    )


def test_a_new_job_resolves_the_current_family_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        ledger_module,
        "accepted_template_identity",
        lambda report_type, output_formats: ("portfolio-review", "v2"),
    )
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-new-default",
    )

    assert job.render_template_version == "v2"


def test_the_envelope_refuses_to_invent_a_template(tmp_path) -> None:
    _ledger, store, ready = _seed_data_ready_job(tmp_path)
    snapshot = store.get_snapshot_by_job(ready.job_id)
    job = _ledger.get_job(ready.job_id)
    stripped = job.__class__(**{**job.__dict__, "render_template_version": None})

    with pytest.raises(ValueError, match="RENDER_PACKAGE_TEMPLATE_IDENTITY_REQUIRED"):
        _build_render_package(
            job=stripped,
            snapshot=snapshot.snapshot_payload,
            render_job_id="rdr_no_identity",
            snapshot_id=snapshot.snapshot_id,
        )


def test_render_response_must_state_the_ordered_pair() -> None:
    package = {"template_id": "portfolio-review", "template_version": "v1"}

    assert (
        template_contract_mismatch(
            package, {"template_id": "portfolio-review", "template_version": "v1"}
        )
        is None
    )
    for response in (
        {"template_id": "portfolio-review", "template_version": "v2"},
        {"template_id": "outcome-review", "template_version": "v1"},
        {},
    ):
        message = template_contract_mismatch(package, response)
        assert message and "RENDER_TEMPLATE_CONTRACT_MISMATCH" in message


def test_backfill_assigns_historical_v1_only_where_it_is_true(tmp_path) -> None:
    """The 017 migration matrix: a pre-cutover PDF job with no recorded
    template gets its family's historical v1 deterministically; a json-only
    job stays untouched; a value recorded at render time is preserved."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    pdf_job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-backfill-pdf",
    )
    json_job = ledger.create_portfolio_review_job(
        request=_job_request(requested_output_formats=["json"]),
        caller_context=_caller(),
        idempotency_key="idem-backfill-json",
    )
    migration = (
        Path(__file__).resolve().parents[3]
        / "migrations"
        / "017_report_job_template_identity_backfill.sql"
    ).read_text(encoding="utf-8")
    with closing(sqlite3.connect(tmp_path / "jobs.sqlite3")) as connection, connection:
        # Recreate the pre-cutover world: acceptance stamped nothing.
        connection.execute(
            "UPDATE report_job SET render_template_id = NULL, render_template_version = NULL"
        )
        # One job already rendered on a recorded template - preserved as-is.
        connection.execute(
            "UPDATE report_job SET render_template_id = 'portfolio-review',"
            " render_template_version = 'v0-recorded'"
            " WHERE report_job_id = ?",
            (json_job.job_id,),
        )
        connection.executescript(migration)
        rows = {
            str(row[0]): (row[1], row[2])
            for row in connection.execute(
                "SELECT report_job_id, render_template_id, render_template_version FROM report_job"
            )
        }

    assert rows[pdf_job.job_id] == ("portfolio-review", "v1")
    assert rows[json_job.job_id] == ("portfolio-review", "v0-recorded")


class _RenderClientWrongTemplate:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 201, {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": payload["template_id"],
            "template_version": "v2",
            "artifact_sha256": "sha256:wrong-template",
            "archive_state": "archived_verified",
            "archive_document_id": "doc_wrong_template",
        }


@pytest.mark.asyncio
async def test_a_mismatched_render_outcome_fails_closed_and_records_no_custody(tmp_path) -> None:
    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientWrongTemplate(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    outcome = await service.render_for_job(ready)

    assert outcome.status == "failed"
    assert outcome.failure_category == "render_validation_failed"
    assert outcome.retry_eligible is False
    assert "RENDER_TEMPLATE_CONTRACT_MISMATCH" in (outcome.failure_message or "")
    # The artifact this job never ordered is not recorded as its custody.
    assert outcome.archive_document_id is None
    assert outcome.render_artifact_sha256 is None
