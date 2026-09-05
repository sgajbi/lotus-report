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
    # The stamped pair IS the current family default - asserted against the
    # definitions so this test follows deliberate default changes.
    assert (job.render_template_id, job.render_template_version) == resolve_report_template(
        "portfolio_review"
    )


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

    monkeypatch.setattr(
        ledger_module,
        "job_template_identity",
        lambda report_type, output_formats, inherited=None: (
            inherited if inherited is not None else ("portfolio-review", "v0-frozen")
        ),
    )
    _ledger, store, ready = _seed_data_ready_job(tmp_path)
    monkeypatch.undo()
    snapshot = store.get_snapshot_by_job(ready.job_id)

    package = _build_render_package(
        job=_ledger.get_job(ready.job_id),
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_immutable",
        snapshot_id=snapshot.snapshot_id,
    )

    assert package["template_version"] == "v0-frozen"
    assert package["render_context"]["document_reference"] == mint_document_reference(
        report_job_id=ready.job_id,
        snapshot_id=snapshot.snapshot_id,
        template_id="portfolio-review",
        template_version="v0-frozen",
    )


def test_a_new_job_resolves_the_current_family_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        ledger_module,
        "job_template_identity",
        lambda report_type, output_formats, inherited=None: (
            inherited if inherited is not None else ("portfolio-review", "v99")
        ),
    )
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")

    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-new-default",
    )

    assert job.render_template_version == "v99"


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
            "template_version": payload["template_version"] + "-not-ordered",
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


class _RenderClientStatingPublication:
    def __init__(self, publication):
        self._publication = publication

    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        response = {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": payload["template_id"],
            "template_version": payload["template_version"],
            "artifact_sha256": "sha256:artifact",
            "artifact_base64": "JVBERi0xLjQ=",
            "archive_state": "archived_verified",
            "archive_document_id": "doc_publication",
        }
        if self._publication is not None:
            response["template_publication"] = self._publication
        return 201, response


@pytest.mark.asyncio
@pytest.mark.parametrize("publication", ["development", "published", None])
async def test_the_render_stated_publication_posture_is_persisted_verbatim(
    tmp_path, publication
) -> None:
    """Render states the template's governance posture AT RENDER TIME; Report
    persists that statement beside the version and custody facts - verbatim,
    including its absence. Custody (archived) and publication stay distinct
    facts; distribution authority is Gateway/Archive-owned, never inferred
    from either."""

    from app.reporting_render.service import PortfolioReviewRenderOrchestrationService

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientStatingPublication(publication),
        snapshot_store=store,
        job_ledger=ledger,
    )

    outcome = await service.render_for_job(ready)

    record = ledger.get_job(ready.job_id)
    assert record.render_template_publication == publication
    assert outcome.status in {"archived", "completed", "archiving"}


def test_failed_work_replay_preserves_the_source_jobs_accepted_contract(
    tmp_path, monkeypatch
) -> None:
    """The audit's reproduced defect, inverted into a regression: a job
    accepted under one template version, replayed after the family default
    moves, must retain its original accepted contract - a replay RECOVERS
    the accepted document; only regenerate resolves the current default."""

    monkeypatch.setattr(
        ledger_module,
        "job_template_identity",
        lambda report_type, output_formats, inherited=None: (
            inherited if inherited is not None else ("portfolio-review", "v1-accepted")
        ),
    )
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-replay-source",
    )
    assert source.render_template_version == "v1-accepted"
    ledger.mark_data_ready(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
    )
    ledger.mark_failed(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
        failure_category="render_execution_failed",
        failure_message="render runtime crashed",
        retry_eligible=True,
    )

    # The deployment moves the family default before the replay.
    monkeypatch.setattr(
        ledger_module,
        "job_template_identity",
        lambda report_type, output_formats, inherited=None: (
            inherited if inherited is not None else ("portfolio-review", "v2-current")
        ),
    )
    derived = ledger.create_replay_derived_job(
        source_job_id=source.job_id,
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-replay-derived",
        reason="Replay of failed report work.",
    )

    assert derived.render_template_version == "v1-accepted"
    assert derived.render_template_id == "portfolio-review"

    # A NEW ordinary job still resolves the current default.
    fresh = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-fresh-after-move",
    )
    assert fresh.render_template_version == "v2-current"


def test_acceptance_persists_every_document_contract_axis(tmp_path) -> None:
    """report#283 finding 6: the whole accepted document contract is one
    durable job fact - family, type, input-snapshot schema, report-data
    contract, envelope version, template pair, locale, brand, and the
    standard-disclosure baseline - resolved once at acceptance."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-contract",
    )

    contract = job.accepted_document_contract
    assert contract is not None
    assert contract["accepted_contract_version"] == "adc.v1"
    assert contract["report_family_id"] == "portfolio_review"
    assert contract["report_type"] == "portfolio_review"
    assert contract["input_snapshot_contract_version"]
    assert contract["report_data_contract_version"] == "portfolio_review.v1"
    assert contract["render_package_version"] == "render_package.v1"
    assert (contract["template_id"], contract["template_version"]) == resolve_report_template(
        "portfolio_review"
    )
    assert contract["locale"] == "en-SG"
    assert contract["brand_variant"] == "private_banking"
    assert contract["standard_disclosure_ref"] == "portfolio-review.standard-disclosures.v1"


def test_a_replayed_job_keeps_its_original_accepted_contract(tmp_path, monkeypatch) -> None:
    """The demonstrated replay defect, closed: a failed v1 job replayed
    after a v2 deployment retains its ORIGINAL accepted contract verbatim -
    a deployment that moved the definitions must not reinterpret it."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    source = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-replay-contract-src",
    )
    original_contract = source.accepted_document_contract
    assert original_contract is not None
    ledger.mark_collecting_data(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
    )
    ledger.mark_failed(
        job_id=source.job_id,
        actor=source.triggered_by,
        correlation_id=source.correlation_id,
        trace_id=source.trace_id,
        failure_category="upstream_data_failed",
        failure_message="upstream unavailable",
        retry_eligible=True,
    )

    # Simulate the v2 deployment: a fresh acceptance would now mint a
    # different contract...
    def _v2_contract(
        report_type,
        output_formats,
        *,
        input_snapshot_contract_version,
        inherited_template=None,
    ):
        return {
            "accepted_contract_version": "adc.v1",
            "report_data_contract_version": "portfolio_review.v2",
            "template_id": "portfolio-review",
            "template_version": "v9",
            "locale": "en-GB",
        }

    monkeypatch.setattr(ledger_module, "accepted_document_contract", _v2_contract)
    fresh = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-fresh-under-v2",
    )
    assert fresh.accepted_document_contract is not None
    assert fresh.accepted_document_contract["report_data_contract_version"] == (
        "portfolio_review.v2"
    )

    # ...but the REPLAY inherits the source's original verbatim.
    replayed = ledger.create_replay_derived_job(
        source_job_id=source.job_id,
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-replay-contract",
        reason="Replay after upstream recovery.",
    )
    assert replayed.accepted_document_contract == original_contract


def test_the_envelope_consumes_the_accepted_pass_through_axes(tmp_path) -> None:
    """Pass-through axes (locale, brand, disclosure baseline) surface from
    the persisted acceptance fact even though today's definitions say
    otherwise - they carry values, not composition shape."""

    _ledger, store, ready = _seed_data_ready_job(tmp_path)
    snapshot = store.get_snapshot_by_job(ready.job_id)
    frozen = ready.model_copy(
        update={
            "accepted_document_contract": {
                **(ready.accepted_document_contract or {}),
                "locale": "en-HK",
                "brand_variant": "retail_banking",
                "standard_disclosure_ref": "portfolio-review.standard-disclosures.v0",
            }
        }
    )

    package = _build_render_package(
        job=frozen,
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_contract_frozen",
        snapshot_id=snapshot.snapshot_id,
    )

    assert package["locale"] == "en-HK"
    assert package["brand_variant"] == "retail_banking"
    assert package["disclosure_refs"][0] == "portfolio-review.standard-disclosures.v0"


def test_a_shape_binding_axis_this_deployment_cannot_compose_fails_closed(
    tmp_path,
) -> None:
    """The composers emit exactly one shape per family: an accepted
    report-data contract this deployment no longer composes REFUSES rather
    than mislabelling a new-shaped payload with the old version. The
    governed remedy is regeneration under the current contract."""

    _ledger, store, ready = _seed_data_ready_job(tmp_path)
    snapshot = store.get_snapshot_by_job(ready.job_id)
    stale = ready.model_copy(
        update={
            "accepted_document_contract": {
                **(ready.accepted_document_contract or {}),
                "report_data_contract_version": "portfolio_review.v0-frozen",
            }
        }
    )

    with pytest.raises(ValueError, match="RENDER_PACKAGE_ACCEPTED_CONTRACT_UNSUPPORTED"):
        _build_render_package(
            job=stale,
            snapshot=snapshot.snapshot_payload,
            render_job_id="rdr_contract_stale",
            snapshot_id=snapshot.snapshot_id,
        )


def test_a_legacy_job_without_a_contract_resolves_current_definitions(tmp_path) -> None:
    """A job accepted before the contract existed keeps working: it
    resolves today's definitions, with no accepted-contract claim implied
    and nothing fabricated."""

    _ledger, store, ready = _seed_data_ready_job(tmp_path)
    snapshot = store.get_snapshot_by_job(ready.job_id)
    legacy = ready.model_copy(update={"accepted_document_contract": None})

    package = _build_render_package(
        job=legacy,
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_legacy_contract",
        snapshot_id=snapshot.snapshot_id,
    )

    assert package["report_data_contract_version"] == "portfolio_review.v1"
    assert package["locale"] == "en-SG"
    assert package["disclosure_refs"][0] == "portfolio-review.standard-disclosures.v1"
