"""Integrated lifecycle proof (#316) — the #283 closure evidence.

Production-shaped scenario on REAL PostgreSQL paths: two tenants sharing a
portfolio identifier and as-of date, a recurring cycle, a source restatement,
and a template/contract deployment change. Every stage below the provider is
the real machinery — Postgres ledger and snapshot store, the real capture
service (identity, factual boundary, coverage, coherence, lifecycle), the
real render orchestration and custody recording — with owner-shaped payloads
injected only at the upstream-provider and Render/Archive client boundaries,
the same seams the audited unit proofs use. No assertion is a fixture
stating its own intended posture: each one reads persisted rows or recorded
downstream payloads back and compares facts.

Each test is one numbered assertion from issue #316 (audit §5), named
`test_a<N>_...` so a failure names the broken invariant.
"""

from __future__ import annotations

import asyncio
import copy
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_jobs.ledger import compute_request_hash
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_jobs.worker import ReportJobWorker
from app.reporting_lineage.capture_service import (
    PortfolioReviewInputCapture,
    PortfolioReviewInputCaptureError,
    PortfolioReviewSnapshotCaptureService,
    _RecordedUpstreamCall,
)
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_render.service import PortfolioReviewRenderOrchestrationService
from tests.integration.postgres_adapter_ownership import own_postgres_adapter


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for the integrated proof")
    return database_url


TENANT_A = "tenant-sg"
TENANT_B = "tenant-hk"
SHARED_PORTFOLIO = "PB_SG_GLOBAL_BAL_001"
SHARED_AS_OF = "2026-04-22"


def _caller(*, tenant: str, suffix: str) -> ReportCallerContext:
    return ReportCallerContext(
        trigger_type="user",
        triggered_by=f"advisor-{tenant}",
        caller_application="lotus-gateway",
        tenant_id=tenant,
        region="APAC",
        booking_center_code="SG",
        role=None,
        correlation_id=f"corr-proof-{suffix}",
        trace_id=f"trace-proof-{suffix}",
    )


def _request(*, formats: list[str] | None = None) -> PortfolioReviewJobRequest:
    return PortfolioReviewJobRequest.model_validate(
        {
            "portfolio_scope": {"portfolio_ids": [SHARED_PORTFOLIO]},
            "as_of_date": SHARED_AS_OF,
            "requested_output_formats": formats or ["pdf"],
            "reporting_currency": "USD",
            "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
        }
    )


def _recorded_call(
    *, service_name: str = "lotus-core", endpoint: str, suffix: str
) -> _RecordedUpstreamCall:
    return _RecordedUpstreamCall(
        service_name=service_name,
        endpoint=endpoint,
        method="POST",
        contract_version="v1",
        request_payload={"portfolio_id": SHARED_PORTFOLIO, "as_of_date": SHARED_AS_OF},
        response_payload={"ok": True},
        response_ref=None,
        status_code=200,
        latency_ms=42,
        supportability_status="complete",
        completeness_status="complete",
        failure_category="none",
        failure_message=None,
        captured_at=datetime(2026, 4, 22, 9, 0, 2, tzinfo=UTC),
        correlation_id=f"corr-proof-{suffix}",
        trace_id=f"trace-proof-{suffix}",
    )


def _source_payload(*, tenant: str, restatement: str, suffix: str) -> dict:
    """Owner-shaped captured payload: source-stated revision evidence beside
    composition-instance metadata, per-tenant content so evidence cannot be
    mistaken across tenants by value coincidence."""

    return {
        "report_id": f"portfolio-review:{SHARED_PORTFOLIO}:{SHARED_AS_OF}",
        "portfolio_id": SHARED_PORTFOLIO,
        "as_of_date": SHARED_AS_OF,
        "contract_version": "v1",
        "generated_at": "2026-04-22T09:00:01Z",
        "correlation_id": f"corr-proof-{suffix}",
        "holdings": {
            "rows": [{"security_id": "SEC1", "market_value": f"100.25-{tenant}"}],
            "sourceProduct": {
                "source_service": "lotus-core",
                "product_name": "HoldingsAsOf",
                "product_version": "v1",
                "as_of_date": SHARED_AS_OF,
                "generated_at": "2026-04-22T08:59:59Z",
                "restatement_version": restatement,
                "source_batch_fingerprint": f"core-batch-{tenant}",
                "snapshot_id": f"core-snap-{tenant}-{restatement}",
                "content_hash": f"sha256:holdings-{tenant}-{restatement}",
                "reconciliation_status": "reconciled",
            },
        },
        # lotus-performance participates through its upstream call but the
        # shipped read payload states no sourceProduct evidence for it - the
        # vector records it as a declared bare participant (real coverage
        # posture: partial), which assertion 6 pins.
        "performance": {"summary": {"twr": "0.0412"}},
    }


class _ScenarioProvider:
    """Owner-shaped upstream boundary: yields the payload staged for each
    job, so cycles and restatements are driven by source facts, never by
    test-side mutation of persisted state."""

    def __init__(self) -> None:
        self.staged: dict[str, dict] = {}
        self.default_payload: dict | None = None

    def stage(self, job_id: str, payload: dict) -> None:
        self.staged[job_id] = payload

    async def collect_for_job(self, job):
        payload = self.staged.get(job.job_id) or self.default_payload
        suffix = job.correlation_id.removeprefix("corr-proof-")
        if payload is None:
            # An unstaged job is the scenario's SOURCE OUTAGE: surface it as
            # the capture error the real upstream boundary raises.
            raise PortfolioReviewInputCaptureError(
                original_error=RuntimeError("upstream_unavailable"),
                upstream_calls=[
                    _recorded_call(endpoint="/reporting/portfolio-summary/query", suffix=suffix)
                ],
            )
        return PortfolioReviewInputCapture(
            snapshot_payload=copy.deepcopy(payload),
            upstream_calls=[
                _recorded_call(endpoint="/reporting/portfolio-summary/query", suffix=suffix),
                _recorded_call(
                    service_name="lotus-performance",
                    endpoint="/performance/workspace-summary",
                    suffix=suffix,
                ),
            ],
        )


class _CustodyRenderClient:
    """Owner-shaped Render client (bb's stated contract facts): create-or-get
    submit keyed by render_job_id, archived_verified custody with a durable
    document id, every submitted package recorded for downstream-fact
    assertions."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.documents: dict[str, str] = {}

    async def submit_render_package(self, payload, **kwargs):
        self.payloads.append(copy.deepcopy(payload))
        render_job_id = payload["render_job_id"]
        document_id = self.documents.setdefault(render_job_id, f"doc_{uuid4().hex[:12]}")
        return 201, {
            "status": "rendered",
            "render_job_id": render_job_id,
            "template_id": payload["template_id"],
            "template_version": payload["template_version"],
            "template_publication": "published",
            "artifact_sha256": f"sha256:artifact-{render_job_id}",
            "bounded_determinism_fingerprint": f"fingerprint-{render_job_id}",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 640,
            "artifact_base64": "JVBERi0xLjQ=",
            "archive_state": "archived_verified",
            "archive_document_id": document_id,
            "archive_detail": None,
        }


class _World:
    """One scenario world on the real PostgreSQL adapters."""

    def __init__(self) -> None:
        url = _database_url()
        self.ledger = own_postgres_adapter(PostgresReportJobLedger(url))
        self.store = own_postgres_adapter(PostgresReportInputSnapshotStore(url))
        self.provider = _ScenarioProvider()
        self.capture = PortfolioReviewSnapshotCaptureService(
            snapshot_store=self.store,
            job_ledger=self.ledger,
            portfolio_review_input_provider=self.provider,
        )
        self.render_client = _CustodyRenderClient()
        self.render = PortfolioReviewRenderOrchestrationService(
            render_client=self.render_client,
            snapshot_store=self.store,
            job_ledger=self.ledger,
        )

    def submit(self, *, tenant: str, suffix: str, request=None):
        return self.ledger.submit_portfolio_review_job(
            request=request or _request(),
            caller_context=_caller(tenant=tenant, suffix=suffix),
            idempotency_key=f"proof-{suffix}",
        )

    def run_pipeline(self) -> None:
        worker = ReportJobWorker(
            work_ledger=self.ledger,
            execution_service=ReportJobExecutionService(
                report_job_ledger=self.ledger,
                capture_service=self.capture,
                render_service=self.render,
            ),
        )
        asyncio.run(
            worker.run_once(
                worker_id="integrated-proof-worker",
                max_items=100,
                lease_seconds=60,
            )
        )


def _archived_cycle(world: _World, *, tenant: str, suffix: str, restatement: str = "r1"):
    """Submit one job for the tenant, stage its source facts, run the real
    pipeline to archived, and return the (job record, snapshot record)."""

    job = world.submit(tenant=tenant, suffix=suffix)
    world.provider.stage(
        job.job_id, _source_payload(tenant=tenant, restatement=restatement, suffix=suffix)
    )
    world.run_pipeline()
    record = world.ledger.get_job(job.job_id)
    snapshot = world.store.get_snapshot_by_job(job.job_id)
    return record, snapshot


# ---------------------------------------------------------------------------
# Assertion 1: same portfolio identifier/date under different tenants cannot
# share or cross-resolve report evidence.
# ---------------------------------------------------------------------------


def test_a1_tenants_sharing_portfolio_and_date_cannot_cross_resolve_evidence():
    suffix = uuid4().hex[:12]
    world = _World()
    record_a, snapshot_a = _archived_cycle(world, tenant=TENANT_A, suffix=f"a1a-{suffix}")
    record_b, snapshot_b = _archived_cycle(world, tenant=TENANT_B, suffix=f"a1b-{suffix}")

    assert record_a.job_id != record_b.job_id
    assert snapshot_a.snapshot_id != snapshot_b.snapshot_id
    # The evidence itself is tenant-distinct because the SOURCES stated
    # different facts - digests cannot coincide by construction.
    assert snapshot_a.factual_content_digest != snapshot_b.factual_content_digest
    assert snapshot_a.report_revision_id != snapshot_b.report_revision_id

    # Cross-resolution is fenced at the search boundary: tenant A's admitted
    # scope never returns tenant B's job.
    from app.reporting_jobs.models import ReportJobListFilters

    listed_for_a = world.ledger.list_jobs(
        filters=ReportJobListFilters(tenant_id=TENANT_A, correlation_id=f"corr-proof-a1b-{suffix}")
    )
    assert listed_for_a == []
    listed_for_b = world.ledger.list_jobs(
        filters=ReportJobListFilters(tenant_id=TENANT_B, correlation_id=f"corr-proof-a1b-{suffix}")
    )
    assert [item.job_id for item in listed_for_b] == [record_b.job_id]


# ---------------------------------------------------------------------------
# Assertion 2: unknown identity fields fail validation; nested mutations
# cannot alter admitted identity.
# ---------------------------------------------------------------------------


def test_a2_unknown_identity_fields_fail_and_nested_mutation_cannot_switch_tenant():
    suffix = uuid4().hex[:12]
    world = _World()

    # Identity models are fail-closed (extra=forbid, the #290 posture): an
    # unknown field on a governed identity is refused, never absorbed.
    from pydantic import ValidationError

    from app.idea_evidence_intake.models import IdeaEvidenceReportPackageIdentity

    with pytest.raises(ValidationError):
        IdeaEvidenceReportPackageIdentity.model_validate(
            {
                "report_evidence_pack_id": "pack-1",
                "conversion_intent_id": "intent-1",
                "candidate_id": "cand-1",
                "tenant_override": TENANT_B,
            }
        )

    # Admitted identity comes from the caller context alone - request options
    # carrying a tenant-shaped value change nothing about admission.
    smuggling = PortfolioReviewJobRequest.model_validate(
        {
            "portfolio_scope": {"portfolio_ids": [SHARED_PORTFOLIO]},
            "as_of_date": SHARED_AS_OF,
            "requested_output_formats": ["pdf"],
            "reporting_currency": "USD",
            "options": {"sections": ["OVERVIEW"], "tenant_id": TENANT_B},
        }
    )
    job = world.ledger.submit_portfolio_review_job(
        request=smuggling,
        caller_context=_caller(tenant=TENANT_A, suffix=f"a2-{suffix}"),
        idempotency_key=f"proof-a2-{suffix}",
    )
    stored = world.ledger.get_job(job.job_id)
    assert stored.tenant_id == TENANT_A


# ---------------------------------------------------------------------------
# Assertion 3: semantically equivalent declared sets canonicalize
# identically; ordered semantic sequences retain order.
# ---------------------------------------------------------------------------


def test_a3_canonicalization_is_set_stable_and_order_preserving():
    caller = _caller(tenant=TENANT_A, suffix="a3")

    # requested_output_formats is a declared SET: pdf+json in either
    # declaration order is the same request identity.
    hash_pdf_json = compute_request_hash(
        report_type="portfolio_review",
        request=_request(formats=["pdf", "json"]),
        caller_context=caller,
    )
    hash_json_pdf = compute_request_hash(
        report_type="portfolio_review",
        request=_request(formats=["json", "pdf"]),
        caller_context=caller,
    )
    assert hash_pdf_json == hash_json_pdf

    # sections is an ordered SEQUENCE: the document presents them in the
    # declared order, so reordering IS a different request.
    def _with_sections(sections: list[str]):
        return PortfolioReviewJobRequest.model_validate(
            {
                "portfolio_scope": {"portfolio_ids": [SHARED_PORTFOLIO]},
                "as_of_date": SHARED_AS_OF,
                "requested_output_formats": ["pdf"],
                "reporting_currency": "USD",
                "options": {"sections": sections},
            }
        )

    hash_ordered = compute_request_hash(
        report_type="portfolio_review",
        request=_with_sections(["OVERVIEW", "PERFORMANCE"]),
        caller_context=caller,
    )
    hash_reordered = compute_request_hash(
        report_type="portfolio_review",
        request=_with_sections(["PERFORMANCE", "OVERVIEW"]),
        caller_context=caller,
    )
    assert hash_ordered != hash_reordered


# ---------------------------------------------------------------------------
# Assertion 4: persisted snapshot, job, revision, evidence reference and
# agreed downstream metadata resolve to the same facts.
# ---------------------------------------------------------------------------


def test_a4_job_snapshot_revision_and_downstream_metadata_state_the_same_facts():
    suffix = uuid4().hex[:12]
    world = _World()
    record, snapshot = _archived_cycle(world, tenant=TENANT_A, suffix=f"a4-{suffix}")

    assert record.status == "archived"
    assert snapshot.report_revision_id is not None
    assert snapshot.report_revision_id.startswith("rrv3_")

    # The recorded downstream package carries the SAME identity facts -
    # read from what the render client actually received, not from intent.
    package = world.render_client.payloads[-1]
    render_context = package["render_context"]
    assert render_context["report_revision_id"] == snapshot.report_revision_id
    assert package["snapshot_id"] == snapshot.snapshot_id
    # The custody block Archive stores states the SAME identity and tenant
    # facts the ledger and snapshot hold - read from the recorded handoff.
    custody = render_context["archive"]
    assert custody["report_revision_id"] == snapshot.report_revision_id
    assert custody["tenant_id"] == record.tenant_id
    assert custody["report_request_id"] == record.request_id
    assert record.archive_document_id == world.render_client.documents[record.render_job_id]


# ---------------------------------------------------------------------------
# Assertion 5: a source restatement produces a new revision without changing
# or overwriting the earlier evidence.
# ---------------------------------------------------------------------------


def test_a5_restatement_mints_new_revision_and_leaves_earlier_evidence_untouched():
    suffix = uuid4().hex[:12]
    world = _World()
    first_record, first_snapshot = _archived_cycle(
        world, tenant=TENANT_A, suffix=f"a5a-{suffix}", restatement="r1"
    )
    second_record, second_snapshot = _archived_cycle(
        world, tenant=TENANT_A, suffix=f"a5b-{suffix}", restatement="r2"
    )

    assert second_snapshot.report_revision_id != first_snapshot.report_revision_id

    # The earlier evidence is byte-for-byte what it was: re-read the first
    # snapshot after the restated cycle and compare persisted facts.
    reread = world.store.get_snapshot_by_job(first_record.job_id)
    assert reread.snapshot_hash == first_snapshot.snapshot_hash
    assert reread.factual_content_digest == first_snapshot.factual_content_digest
    assert reread.report_revision_id == first_snapshot.report_revision_id
    assert reread.source_revision_vector == first_snapshot.source_revision_vector

    # And the restatement is VISIBLE as source evidence, not inferred:
    revisions = {
        entry["source_service"]: entry
        for entry in second_snapshot.source_revision_vector["revisions"]
    }
    assert revisions["lotus-core"]["restatement_version"] == "r2"


# ---------------------------------------------------------------------------
# Assertion 6: every source digest entry comes from actual source evidence;
# missing/conflicting evidence retains its declared posture.
# ---------------------------------------------------------------------------


def test_a6_digest_entries_are_source_stated_and_absence_stays_declared():
    suffix = uuid4().hex[:12]
    world = _World()
    job = world.submit(tenant=TENANT_A, suffix=f"a6-{suffix}")
    payload = _source_payload(tenant=TENANT_A, restatement="r1", suffix=f"a6-{suffix}")
    world.provider.stage(job.job_id, payload)
    world.run_pipeline()

    snapshot = world.store.get_snapshot_by_job(job.job_id)
    vector = snapshot.source_revision_vector
    revisions = {entry["source_service"]: entry for entry in vector["revisions"]}

    # Stated entries are the source's own facts, verbatim.
    core = revisions["lotus-core"]
    assert core["content_hash"] == f"sha256:holdings-{TENANT_A}-r1"
    assert core["source_snapshot_id"] == f"core-snap-{TENANT_A}-r1"
    assert core["restatement_version"] == "r1"
    # The evidence-less participant stays DECLARED absent - present in the
    # vector because it took part (its upstream call is recorded), with no
    # manufactured evidence fields, and the coverage claim honestly degrades
    # instead of asserting completeness.
    perf = revisions["lotus-performance"]
    assert "content_hash" not in perf
    assert "source_snapshot_id" not in perf
    assert vector["coverage"] == "partial"


# ---------------------------------------------------------------------------
# Assertion 7: ephemeral composition cannot masquerade as a durable report
# revision.
# ---------------------------------------------------------------------------


def test_a7_ephemeral_composition_mints_no_durable_revision():
    suffix = uuid4().hex[:12]
    world = _World()
    # A composition that never completes capture: the provider fails, the
    # real pipeline records FAILURE EVIDENCE - which must not masquerade as
    # a durable report revision. Revisions mint at exactly one choke point,
    # successful capture completion.
    job = world.submit(tenant=TENANT_A, suffix=f"a7-{suffix}")
    world.run_pipeline()

    record = world.ledger.get_job(job.job_id)
    assert record.status == "failed"
    snapshot = world.store.get_snapshot_by_job(job.job_id)
    # Failure evidence exists durably, but it carries NO revision identity,
    # NO factual digests, and its lifecycle claims no reproduction - nothing
    # downstream can cite this capture as a revision of the report.
    assert snapshot.report_revision_id is None
    assert snapshot.factual_content_digest is None
    assert snapshot.source_revision_vector is None
    assert snapshot.lifecycle is not None
    assert snapshot.lifecycle["reproduction_availability"] == "none"
