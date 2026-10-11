"""Frozen v8 recovery with controlled Render/Archive ports; no financial acceptance claim."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.composite_reporting.invocation_authority import binding_for_job
from app.composite_reporting.models import CompositeReviewJobRequest
from app.reporting_identity.capture_binding import revision_for_capture
from app.reporting_jobs.ledger import InvalidReportJobTransitionError, ReportJobLedger
from app.reporting_jobs.models import ReportCallerContext, ReportJobReplayRequest
from app.reporting_lineage.store import ReportInputSnapshotCreateRequest, ReportInputSnapshotStore
from app.reporting_render.replay_service import PortfolioReviewReplayService
from app.reporting_render.service import PortfolioReviewRenderOrchestrationService
from tests.unit.composite_reporting.authority_contract_flow import contract_admission
from tests.unit.composite_reporting.cohesive_examples import cohesive_example
from tests.unit.composite_reporting.test_cohesive_context import cohesive_dataset

NATIVE_ARCHIVE_ABSENCE = json.loads(
    (Path(__file__).parent / "wire_fixtures/archive-ec85-request-absence.json").read_text()
)["response"]


class SourceOff:
    async def capture_for_job(self, job):
        pytest.fail("Custody recovery must never acquire current source data")


class RenderBoundary:
    def __init__(self, state="archive_pending", detail=None, response_overrides=None):
        self.state, self.detail, self.packages = state, detail, []
        self.response_overrides = response_overrides or {}

    async def submit_render_package(self, payload, **kwargs):
        self.packages.append(deepcopy(payload))
        return 201, {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": payload["template_id"],
            "template_version": payload["template_version"],
            "artifact_sha256": "f" * 64,
            "archive_state": self.state,
            "archive_request_id": "areq_" + payload["render_job_id"],
            "archive_document_id": "doc_recovered" if self.state == "archived_verified" else None,
            "archive_detail": self.detail,
            **self.response_overrides,
        }


class Resolver:
    def __init__(self, status=404, payload=None):
        self.status = status
        self.payload = deepcopy(NATIVE_ARCHIVE_ABSENCE) if payload is None else payload
        self.calls = []

    async def get_document_by_request_id(self, request_id, **context):
        self.calls.append((request_id, context))
        return self.status, self.payload


async def failed_source(
    ledger,
    store,
    *,
    corrected=False,
    state="archive_pending",
    detail=None,
    response_overrides=None,
    request_options=None,
):
    request = CompositeReviewJobRequest.model_validate(cohesive_example(corrected)[0])
    if request_options:
        request = request.model_copy(update={"options": {**request.options, **request_options}})
    caller = ReportCallerContext(
        triggered_by="recovery-operator",
        caller_application="lotus-gateway",
        tenant_id=request.selection.tenant_id,
        region="APAC",
        correlation_id="corr-recovery",
        trace_id="trace-recovery",
    )
    job = ledger.submit_composite_review_job(
        request=request,
        caller_context=caller,
        idempotency_key=uuid4().hex,
        twr_admission=contract_admission(request),
    )
    item = ledger.claim_work_items(worker_id="custody-fixture", limit=1, lease_seconds=30)[0]
    assert item.report_job_id == job.job_id
    ledger.complete_work_item(work_item_id=item.work_item_id, lease_token=item.lease_token)
    payload = cohesive_dataset(corrected)
    identity, vector = revision_for_capture(
        job=job, snapshot_payload=payload, upstream_services=["lotus-performance", "lotus-manage"]
    )
    record = store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="composite_review.v8",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=payload,
            report_revision_id=identity.report_revision_id,
            series_digest=identity.series_digest,
            source_revision_digest=identity.source_revision_digest,
            factual_content_digest=identity.factual_content_digest,
            factual_boundary_version=identity.factual_boundary_version,
            source_revision_vector=vector.canonical(),
            source_cut_coherence={},
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    for transition in (ledger.mark_collecting_data, ledger.mark_data_ready):
        job = transition(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    boundary = RenderBoundary(state, detail, response_overrides)
    render = PortfolioReviewRenderOrchestrationService(
        render_client=boundary, snapshot_store=store, job_ledger=ledger
    )
    job = await render.render_for_job(job)
    assert job.status == "failed", job.failure_message
    return job, record, caller, boundary


def recovery(ledger, store, resolver, boundary):
    return PortfolioReviewReplayService(
        ledger=ledger,
        snapshot_store=store,
        archive_resolver=resolver,
        capture_service=SourceOff(),
        render_service=PortfolioReviewRenderOrchestrationService(
            render_client=boundary, snapshot_store=store, job_ledger=ledger
        ),
    )


async def replay(service, job, caller, key="recovery"):
    return await service.replay_job(
        job_id=job.job_id,
        caller_context=caller,
        command=ReportJobReplayRequest(reason="Recover custody."),
        idempotency_key=key,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected", [False, True])
async def test_clean_absence_recovers_frozen_v8_and_same_key_converges(tmp_path, corrected):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, record, caller, first = await failed_source(ledger, store, corrected=corrected)
    assert source.retry_eligible
    boundary, resolver = RenderBoundary("archived_verified"), Resolver()
    service = recovery(ledger, store, resolver, boundary)
    result = await replay(service, source, caller)
    child = result.replayed_job
    assert child.status == "archived", child.failure_message
    assert child.report_type == "composite_review"
    assert child.accepted_document_contract == source.accepted_document_contract
    assert binding_for_job(child).admission == binding_for_job(source).admission
    clone = store.get_snapshot_by_job(child.job_id)
    for field in (
        "snapshot_payload",
        "snapshot_hash",
        "report_revision_id",
        "series_digest",
        "source_revision_digest",
        "factual_content_digest",
        "source_revision_vector",
    ):
        assert getattr(clone, field) == getattr(record, field)
    assert boundary.packages[0]["report_data"] == first.packages[0]["report_data"]
    assert (
        boundary.packages[0]["render_context"]["archive"]["composite_report_identity"]
        == (first.packages[0]["render_context"]["archive"]["composite_report_identity"])
    )
    assert not store.list_upstream_calls(clone.snapshot_id)
    assert (await replay(service, source, caller)).replayed_job.job_id == child.job_id
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(service, source, caller, "different")
    assert len(boundary.packages) == 1
    assert resolver.calls[0][0] == source.archive_request_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "tenant_id",
        "report_job_id",
        "archive_request_id",
        "report_revision_id",
        "composite_report_identity",
        "checksum",
        "snapshot_id",
        "render_job_id",
        "template_version",
        "output_format",
        "document_id",
    ],
)
async def test_lookup_adopts_only_exact_original_custody(tmp_path, mutation):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, record, caller, first = await failed_source(ledger, store)
    payload = {
        **first.packages[0]["render_context"]["archive"],
        **{
            key: first.packages[0][key]
            for key in (
                "snapshot_id",
                "render_job_id",
                "report_type",
                "template_id",
                "template_version",
                "report_data_contract_version",
            )
        },
        "report_job_id": source.job_id,
        "archive_request_id": source.archive_request_id,
        "document_id": "doc_original",
        "output_format": "xlsx",
        "checksum": "f" * 64,
        "declared_artifact_sha256": "f" * 64,
    }
    if mutation:
        payload[mutation] = (
            {}
            if mutation == "composite_report_identity"
            else 123
            if mutation == "document_id"
            else "foreign"
        )
    boundary, resolver = RenderBoundary("archived_verified"), Resolver(200, payload)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, resolver, boundary), source, caller)
    resolved = ledger.get_job(source.job_id)
    assert resolved.status == ("failed" if mutation else "archived")
    assert resolved.archive_document_id == (None if mutation else "doc_original")
    assert not boundary.packages


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 409, 422, 500, 503])
async def test_unavailable_or_refused_lookup_has_no_replacement(tmp_path, status):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(ledger, store)
    boundary = RenderBoundary("archived_verified")
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, Resolver(status), boundary), source, caller)
    assert ledger.get_job(source.job_id).status == "failed"
    assert not boundary.packages


@pytest.mark.asyncio
async def test_definitive_archive_refusal_is_terminal(tmp_path):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(
        ledger, store, state="archive_failed", detail="archive_refused_422: invalid custody"
    )
    assert not source.retry_eligible
    resolver, boundary = Resolver(), RenderBoundary("archived_verified")
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, resolver, boundary), source, caller)
    assert not resolver.calls and not boundary.packages


@pytest.mark.asyncio
async def test_crash_after_snapshot_clone_resumes_without_recollection(tmp_path, monkeypatch):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(ledger, store)
    boundary, resolver = RenderBoundary("archived_verified"), Resolver()
    service = recovery(ledger, store, resolver, boundary)
    mark_ready = ledger.mark_data_ready

    def crash(**kwargs):
        raise RuntimeError("process stopped after durable clone")

    monkeypatch.setattr(ledger, "mark_data_ready", crash)
    with pytest.raises(RuntimeError, match="durable clone"):
        await replay(service, source, caller)
    monkeypatch.setattr(ledger, "mark_data_ready", mark_ready)
    child = (await replay(service, source, caller)).replayed_job
    assert child.status == "archived"
    assert len(boundary.packages) == 1
    assert (
        len(
            [
                e
                for e in ledger.list_status_events(child.job_id)
                if e.event_type == "job_replay_snapshot_cloned"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["archive_failed", None])
async def test_ambiguous_child_blocks_novel_root_replacement(tmp_path, state):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(ledger, store)
    boundary = RenderBoundary(state, "archive_unreachable: lost response")
    service = recovery(ledger, store, Resolver(), boundary)
    child = (await replay(service, source, caller)).replayed_job
    assert (
        child.failure_category
        == ("archive_handoff_failed" if state else "archive_handoff_not_configured")
        and child.retry_eligible
    )
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(service, source, caller, "new-root-key")
    assert len(boundary.packages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "version",
        "format",
        "family",
        "failure",
        "foreign",
        "snapshot",
        "contract_missing",
        "contract_version",
        "template",
    ],
)
async def test_unsupported_or_unbound_recovery_refuses_without_render(tmp_path, change):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(ledger, store)
    from app.reporting_render.replay_service import assert_replay_eligible

    changes = {
        "version": {"render_template_version": "v7"},
        "format": {"requested_output_formats": ["json"]},
        "family": {"report_type": "proof_pack"},
        "failure": {"failure_category": "upstream_data_failed"},
        "contract_missing": {"accepted_document_contract": None},
        "contract_version": {
            "accepted_document_contract": {"report_data_contract_version": "composite_review.v7"}
        },
        "template": {"render_template_id": "foreign-template"},
    }
    if change in changes:
        with pytest.raises(InvalidReportJobTransitionError):
            assert_replay_eligible(source.model_copy(update=changes[change]))
        return
    resolver, boundary = Resolver(), RenderBoundary("archived_verified")
    if change == "foreign":
        from app.reporting_jobs.ledger import ReportJobNotFoundError

        with pytest.raises(ReportJobNotFoundError):
            await replay(
                recovery(ledger, store, resolver, boundary),
                source,
                caller.model_copy(update={"tenant_id": "foreign"}),
            )
        assert not resolver.calls
    else:
        with pytest.raises(InvalidReportJobTransitionError):
            await replay(recovery(ledger, None, resolver, boundary), source, caller)
    assert not boundary.packages


@pytest.mark.asyncio
async def test_derived_request_cannot_change_accepted_options(tmp_path):
    from app.reporting_jobs.replay_policy import composite_replay_request

    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(ledger, store)
    request = composite_replay_request(source).model_copy(update={"options": {"title": "changed"}})
    with pytest.raises(InvalidReportJobTransitionError):
        ledger.create_replay_derived_job(
            source_job_id=source.job_id,
            request=request,
            caller_context=caller,
            idempotency_key="forged",
            reason="Recovery.",
        )
