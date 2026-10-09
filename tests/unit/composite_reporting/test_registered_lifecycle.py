"""Actual registered API/worker/SQLite lifecycle with a named controlled supplier.

This proof is not PostgreSQL, real Performance, Render, Archive or Excel acceptance.
"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest

from app.main import app
from app.report_ordering_catalogue.definitions import REPORT_FAMILY_DEFINITIONS
from app.report_ordering_catalogue.validation import validate_report_ordering_submission
from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_jobs.worker import ReportJobWorker
from app.reporting_lineage.capture_service import PortfolioReviewSnapshotCaptureService
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.package_builder import _build_render_package
from app.reporting_render.service import PortfolioReviewRenderOrchestrationService
from app.routers.report_jobs import get_report_lineage_store
from tests.unit.composite_reporting.fixtures import calculated_example, selection_for

HEADERS = {
    "X-Actor-Id": "actor-a",
    "X-Caller-Application": "lotus-gateway",
    "X-Tenant-Id": "tenant-a",
    "X-Region": "APAC",
    "Idempotency-Key": "composite-original",
}


class NoDocumentRender:
    async def render_for_job(self, job):
        pytest.fail("JSON milestone must not bypass missing Excel suppliers")


@contextmanager
def composite_lifecycle(tmp_path, monkeypatch, *, adapters=None, render_client=None):
    ledger, store = (
        adapters()
        if adapters is not None
        else (
            ReportJobLedger(tmp_path / "jobs.sqlite3"),
            ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3"),
        )
    )
    supplier = {"response": calculated_example(), "calls": []}

    async def controlled_source(**kwargs):
        supplier["calls"].append(deepcopy(kwargs))
        return 200, deepcopy(supplier["response"])

    monkeypatch.setattr("app.clients.performance_client.post_with_retry", controlled_source)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    app.dependency_overrides[get_report_lineage_store] = lambda: store
    worker = ReportJobWorker(
        work_ledger=ledger,
        execution_service=ReportJobExecutionService(
            report_job_ledger=ledger,
            capture_service=PortfolioReviewSnapshotCaptureService(
                snapshot_store=store, job_ledger=ledger
            ),
            render_service=(
                NoDocumentRender()
                if render_client is None
                else PortfolioReviewRenderOrchestrationService(
                    render_client=render_client, snapshot_store=store, job_ledger=ledger
                )
            ),
        ),
    )
    try:
        yield ledger, store, worker, supplier
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    with composite_lifecycle(tmp_path, monkeypatch) as stack:
        yield stack


@pytest.mark.asyncio
async def test_order_worker_capture_retained_retrieval_and_two_tenant_isolation(lifecycle):
    ledger, store, worker, supplier = lifecycle
    request = {"selection": selection_for(supplier["response"]).model_dump(mode="json")}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://report"
    ) as client:
        ordered = await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
        assert ordered.status_code == 202, ordered.text
        job_id = ordered.json()["report_job_id"]
        assert supplier["calls"] == []
        replay = await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
        assert replay.json()["report_job_id"] == job_id
        completed = await worker.run_once(
            worker_id="composite-worker", max_items=1, lease_seconds=30
        )
        assert completed.completed_count == 1
        assert ledger.get_job(job_id).status == "data_ready"
        snapshot = store.get_snapshot_by_job(job_id)
        assert snapshot.snapshot_payload["source_response"] == supplier["response"]
        assert snapshot.report_revision_id is not None
        revisions = snapshot.source_revision_vector["revisions"]
        assert revisions[0]["source_service"] == "lotus-performance"
        assert (
            revisions[0]["content_hash"]
            == supplier["response"]["selection_manifest"]["calculation_fingerprint"]
        )
        assert snapshot.portfolio_scope == {"composite_id": "NEUTRAL_BALANCED"}
        original_hash = snapshot.snapshot_hash
        original_revision = snapshot.report_revision_id
        # Today's supplier may change. Retained retrieval must not touch it.
        supplier["response"]["periods"][0]["return_value"] = "999"
        retrieved = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=HEADERS)
        assert retrieved.status_code == 200, retrieved.text
        assert retrieved.json()["snapshot_hash"] == original_hash
        assert retrieved.json()["report_revision_id"] == original_revision
        assert (
            retrieved.json()["snapshot_payload"]["source_response"]["periods"][0]["return_value"]
            == "0.010000000000"
        )
        assert len(supplier["calls"]) == 1
        foreign = {**HEADERS, "X-Tenant-Id": "tenant-b"}
        assert (
            await client.get(f"/reports/jobs/{job_id}/snapshot", headers=foreign)
        ).status_code == 404
        assert (
            await client.post("/reports/composite-reviews", json=request, headers=foreign)
        ).status_code == 400
        tenant_b_request = deepcopy(request)
        tenant_b_request["selection"]["tenant_id"] = "tenant-b"
        tenant_b = await client.post(
            "/reports/composite-reviews", json=tenant_b_request, headers=foreign
        )
        assert tenant_b.status_code == 202
        assert tenant_b.json()["report_job_id"] != job_id


@pytest.mark.asyncio
async def test_missing_month_is_failed_immutable_evidence_not_false_empty(lifecycle):
    ledger, store, worker, supplier = lifecycle
    request = {"selection": selection_for(supplier["response"]).model_dump(mode="json")}
    supplier["response"]["periods"].pop()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://report"
    ) as client:
        response = await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
        assert response.status_code == 202
        job_id = response.json()["report_job_id"]
        await worker.run_once(worker_id="composite-worker", max_items=1, lease_seconds=30)
        assert ledger.get_job(job_id).status == "failed"
        snapshot = store.get_snapshot_by_job(job_id)
        assert snapshot.snapshot_payload["capture_status"] == "failed"
        assert snapshot.snapshot_payload["composite_id"] == "NEUTRAL_BALANCED"
        assert "portfolio_id" not in snapshot.snapshot_payload
        assert "source_response" not in snapshot.snapshot_payload
        assert snapshot.report_revision_id is None
        calls = store.list_upstream_calls(snapshot.snapshot_id)
        assert calls[0].supportability_status == "error"
        assert calls[0].response_hash is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("format_id", ["xlsx", "pdf", "csv"])
async def test_unaccepted_output_format_cannot_create_a_document_job(lifecycle, format_id):
    _, _, _, supplier = lifecycle
    request = {
        "selection": selection_for(supplier["response"]).model_dump(mode="json"),
        "requested_output_formats": [format_id],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://report"
    ) as client:
        response = await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
        assert response.status_code == 422
    assert supplier["calls"] == []


class ControlledRenderBoundary:
    """Retain the actual worker-emitted package; deliberately decline rendering."""

    def __init__(self):
        self.packages = []

    async def submit_render_package(self, payload, **kwargs):
        self.packages.append(deepcopy(payload))
        return 503, {"failure_message": "Controlled candidate boundary; no workbook claim."}


async def exercise_candidate_worker_package(tmp_path, monkeypatch, *, adapters=None, options=None):
    # A bounded candidate definition is supplied ONLY in this fixture. Every
    # actual submission validator still runs. Production definitions deny xlsx.
    definitions = tuple(
        replace(definition, supported_output_formats=("json", "xlsx"))
        if definition.report_type == "composite_review"
        else definition
        for definition in REPORT_FAMILY_DEFINITIONS
    )

    def candidate_submission(**kwargs):
        return validate_report_ordering_submission(**kwargs, definitions=definitions)

    monkeypatch.setattr(
        "app.routers.report_ordering_validation.validate_report_ordering_submission",
        candidate_submission,
    )
    boundary = ControlledRenderBoundary()
    with composite_lifecycle(
        tmp_path, monkeypatch, adapters=adapters, render_client=boundary
    ) as stack:
        ledger, store, worker, supplier = stack
        request = {
            "selection": selection_for(supplier["response"]).model_dump(mode="json"),
            "requested_output_formats": ["xlsx"],
            "options": options or {},
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews", json=request, headers=HEADERS
            )
            assert response.status_code == 202, response.text
            job_id = response.json()["report_job_id"]
            await worker.run_once(worker_id="composite-candidate", max_items=1, lease_seconds=30)
        assert len(boundary.packages) == 1, ledger.get_job(job_id).failure_message
        package = boundary.packages[0]
        record = store.get_snapshot_by_job(job_id)
        job = ledger.get_job(job_id)
        assert job.status == "failed"
        assert job.failure_category == "render_execution_failed"
        assert job.retry_eligible is True
        assert job.render_output_format == "xlsx"
        assert job.archive_document_id is None
        assert package["report_data"] == record.snapshot_payload
        assert package["report_data"]["source_response"] == supplier["response"]
        assert package["output_format"] == "xlsx"
        assert package["template_id"] == "composite-review"
        archive = package["render_context"]["archive"]
        assert archive["portfolio_scope"] == "composite"
        assert archive["portfolio_id"] is None
        assert archive["composite_id"] == "NEUTRAL_BALANCED"
        assert archive["report_revision_id"] == record.report_revision_id
        for field, value in (options or {}).items():
            assert archive[field] == value
        identity = archive["composite_report_identity"]
        assert identity["publication_state"] == "NOT_ATTESTED"
        for field in ("series_digest", "source_revision_digest", "factual_content_digest"):
            assert identity[field] == getattr(record, field).removeprefix("sha256:")
        # Guard rejection is exercised with representative persisted-identity
        # conflicts, not merely with a duplicated implementation oracle.
        for field, value in (
            ("report_job_id", "foreign-job"),
            ("series_digest", None),
            ("source_revision_digest", "sha256:" + "f" * 64),
            ("source_revision_vector", {}),
        ):
            invalid = record.model_copy(update={field: value})
            with pytest.raises(ValueError, match="COMPOSITE_"):
                _build_render_package(
                    job=job,
                    snapshot=invalid.snapshot_payload,
                    render_job_id=package["render_job_id"],
                    snapshot_id=invalid.snapshot_id,
                    report_revision_id=invalid.report_revision_id,
                    snapshot_record=invalid,
                )
        return {
            "qualification": "CONTROLLED_PERFORMANCE_CANDIDATE_ADMISSION_REAL_REPORT_PRODUCER",
            "limits": [
                "Synthetic Performance source",
                "Test-only xlsx family admission",
                "Controlled Render 503 boundary",
                "No workbook or Archive completion",
            ],
            "request": request,
            "source_response": supplier["response"],
            "snapshot": record.model_dump(mode="json"),
            "job": job.model_dump(mode="json"),
            "render_package": package,
        }


@pytest.mark.asyncio
async def test_registered_candidate_worker_emits_persisted_composite_package(tmp_path, monkeypatch):
    await exercise_candidate_worker_package(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_candidate_preserves_explicit_retention_options_without_inventing_policy(
    tmp_path, monkeypatch
):
    await exercise_candidate_worker_package(
        tmp_path,
        monkeypatch,
        options={
            "retention_policy_id": "synthetic-retention-policy",
            "retain_until_date": "2027-02-28",
        },
    )
