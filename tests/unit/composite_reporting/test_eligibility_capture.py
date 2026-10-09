"""Real Report client/worker paths with explicitly controlled transport responses."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from jsonschema import Draft202012Validator

from app.clients.manage_client import ManageClient
from app.composite_reporting.eligibility_contract import composite_eligibility_report_schema
from app.composite_reporting.eligibility_tables import (
    build_eligibility_dataset,
    eligibility_workbook_projection,
    preflight_eligibility_package,
)
from app.composite_reporting.models import CompositeReviewJobRequest
from app.composite_reporting.source_capture import CompositeInputProvider
from app.config import settings
from app.reporting_identity.capture_binding import source_revision_vector_for_capture
from app.reporting_lineage.capture_service import PortfolioReviewInputCaptureError
from tests.unit.composite_reporting.fixtures import calculated_example
from tests.unit.composite_reporting.test_eligibility_contract import evaluated_example
from tests.unit.composite_reporting.test_eligibility_history import published_example
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    app,
    composite_lifecycle,
)
from tests.unit.composite_reporting.test_source_capture import job_for


@pytest.mark.asyncio
async def test_cumulative_whole_source_budget_refuses_before_snapshot():
    first, _ = evaluated_example()
    second, _ = evaluated_example("2026-08")
    selection = second.model_copy(
        update={
            "period_start": first.period_start,
            "months": first.months + second.months,
        }
    )
    calls = []

    class Source:
        async def read_eligibility(self, **kwargs):
            calls.append(kwargs)
            return 200, {"unit_test_padding": "x" * 5_000_000}

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(manage_client=Source()).collect_for_job(
            eligibility_job(selection)
        )
    assert "CAPTURE_CAPACITY_EXCEEDED" in str(caught.value.original_error)
    assert len(calls) == 2
    assert caught.value.upstream_calls[-1].supportability_status == "error"


def eligibility_job(selection):
    return job_for(calculated_example()).model_copy(
        update={
            "tenant_id": selection.tenant_id,
            "portfolio_scope": {"composite_id": selection.composite_id},
            "as_of_date": selection.period_end,
            "reporting_currency": selection.reporting_currency,
            "options": {"composite_eligibility_selection": selection.model_dump(mode="json")},
            "accepted_document_contract": {
                "input_snapshot_contract_version": "composite_review.v4"
            },
        }
    )


@pytest.mark.asyncio
async def test_evaluated_source_is_one_exact_get_with_full_unknowns_and_partial_lineage(
    monkeypatch,
):
    selection, months = evaluated_example()
    sent = []

    async def get(**kwargs):
        sent.append(deepcopy(kwargs))
        return 200, deepcopy(months[0]["proposal"])

    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", get)
    source = ManageClient(
        base_url="http://manage", actor_id="configured-report-reader", timeout_seconds=1
    )
    capture = await CompositeInputProvider(manage_client=source).collect_for_job(
        eligibility_job(selection)
    )
    assert len(sent) == 1
    assert (
        sent[0]["url"]
        == "http://manage/api/v1/rebalance/composites/test-composite/definitions/d1/monthly-eligibility/evaluations/eval-2026-07"
    )
    assert sent[0]["headers"]["X-Tenant-Id"] == "test-tenant"
    assert sent[0]["headers"]["X-Actor-Id"] == "configured-report-reader"
    assert sent[0]["headers"]["X-Role"] == "REPORT_COMPOSITE_READER"
    assert sent[0]["headers"]["X-Correlation-Id"] == "corr-composite"
    assert sent[0]["headers"]["X-Trace-Id"] == "trace-composite"
    assert capture.snapshot_payload["source_months"] == months
    call = capture.upstream_calls[0].to_create_request()
    assert call.service_name == "lotus-manage"
    assert call.supportability_status == call.completeness_status == "partial"
    vector = source_revision_vector_for_capture(
        snapshot_payload=capture.snapshot_payload, upstream_services=("lotus-manage",)
    )
    revision = vector.canonical()["revisions"][0]
    assert revision["source_service"] == "lotus-manage"
    assert revision["source_product"] == "CompositeMonthlyEvaluationProposal"
    assert revision.get("calculation_run_id") is None


@pytest.mark.asyncio
async def test_published_uses_universe_locator_and_exact_five_read_operations():
    selection, months = published_example()
    month = months[0]
    calls = []

    class Source:
        async def read_eligibility(self, **kwargs):
            calls.append(kwargs)
            path = kwargs["endpoint"]
            key = (
                "receipt"
                if kwargs["binding"] is not None
                else (
                    "universe"
                    if "universe-attestations" in path
                    else "publication"
                    if "/publications/" in path
                    else "parent_membership"
                    if path.endswith("/parent-r1")
                    else "membership"
                )
            )
            return 200, deepcopy(month[key])

    capture = await CompositeInputProvider(manage_client=Source()).collect_for_job(
        eligibility_job(selection)
    )
    assert len(calls) == 5
    assert [call["binding"] is not None for call in calls] == [False, False, True, False, False]
    assert calls[2]["binding"]["digest"] == selection.months[0].approval_content_hash
    assert calls[2]["binding"]["digest"] != month["receipt"]["approval"]["claims_digest"]
    assert capture.snapshot_payload["source_months"] == months


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload", [(200, {}), (202, {}), (404, {}), (503, {})])
async def test_unavailable_or_empty_source_retains_error_not_zero(status, payload):
    selection, _ = evaluated_example()

    class Source:
        async def read_eligibility(self, **kwargs):
            return status, payload

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(manage_client=Source()).collect_for_job(
            eligibility_job(selection)
        )
    assert caught.value.upstream_calls[0].supportability_status == "error"
    assert caught.value.upstream_calls[0].failure_category == "upstream_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,tenant",
    [("", "test-tenant"), ("a,b", "test-tenant"), ("reader", "a,b"), ("reader\nforged", "tenant")],
)
async def test_manage_identity_refuses_before_transport(actor, tenant, monkeypatch):
    async def forbidden(**kwargs):
        pytest.fail("Invalid trusted read identity reached transport")

    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", forbidden)
    with pytest.raises(ValueError):
        await ManageClient(
            base_url="http://manage", actor_id=actor, timeout_seconds=1
        ).read_eligibility(
            endpoint="/api/v1/rebalance/composites/c/definitions/d",
            binding=None,
            admitted_tenant_id=tenant,
        )


@pytest.mark.asyncio
async def test_actual_order_worker_snapshot_retrieval_uses_manage_and_preserves_retained_source(
    tmp_path, monkeypatch
):
    selection, months = evaluated_example()
    sent = []

    async def source(**kwargs):
        sent.append(kwargs)
        return 200, deepcopy(months[0]["proposal"])

    monkeypatch.setattr(settings, "manage_read_actor_id", "configured-reader")
    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", source)
    with composite_lifecycle(tmp_path, monkeypatch) as (ledger, store, worker, performance):
        headers = {**HEADERS, "X-Tenant-Id": "test-tenant"}
        request = {"eligibility_selection": selection.model_dump(mode="json")}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            ordered = await client.post("/reports/composite-reviews", json=request, headers=headers)
            assert ordered.status_code == 202, ordered.text
            job_id = ordered.json()["report_job_id"]
            job = ledger.get_job(job_id)
            assert (
                job.accepted_document_contract["input_snapshot_contract_version"]
                == "composite_review.v4"
            )
            completed = await worker.run_once(
                worker_id="eligibility-worker", max_items=1, lease_seconds=30
            )
            assert completed.completed_count == 1
            assert ledger.get_job(job_id).status == "data_ready"
            snapshot = store.get_snapshot_by_job(job_id)
            assert snapshot.snapshot_payload["source_months"] == months
            before = snapshot.snapshot_hash
            months[0]["proposal"]["evaluation"]["portfolios"].clear()
            retained = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=headers)
            assert retained.status_code == 200
            assert retained.json()["snapshot_hash"] == before
            assert (
                len(
                    retained.json()["snapshot_payload"]["source_months"][0]["proposal"][
                        "evaluation"
                    ]["portfolios"]
                )
                == 2
            )
            assert len(sent) == 1
            assert performance["calls"] == []


def workbook_package():
    selection, months = evaluated_example()
    return {
        "render_package_version": "v1",
        "render_job_id": "test-render",
        "report_job_id": "test-report",
        "snapshot_id": "test-snapshot",
        "template_id": "composite-review",
        "template_version": "v4",
        "report_data_contract_version": "composite_review.v4",
        "output_format": "xlsx",
        "lineage_refs": ["test-snapshot"],
        "disclosure_refs": [],
        "render_context": {},
        "report_data": build_eligibility_dataset(selection, months),
    }


def test_workbook_preflight_counts_hidden_evidence_headers_and_chunks():
    package = workbook_package()
    totals = preflight_eligibility_package(package)
    visible_cells = sum(
        len(table["columns"]) * len(table["rows"]) for table in package["report_data"]["tables"]
    )
    assert (
        totals["sheets"] == 12
    )  # eight visible plus CellEvidence/ColumnPolicy/ArtifactIdentity/PinnedData
    assert totals["total_cells"] > visible_cells * 8
    assert totals["request_body_bytes"] == len(
        json.dumps(package, ensure_ascii=False, separators=(",", ":")).encode()
    )
    # Literal 12-sheet writer oracle, including the frozen v4 rounding statement.
    assert totals["total_text_bytes"] == 83315
    identity_rows = dict(eligibility_workbook_projection(package)[10][1])
    assert json.loads(identity_rows["template_digest"]) == "sha256:" + "0" * 64
    assert json.loads(identity_rows["display_rounding"]) == (
        "Declared column decimal places; HALF_UP; source ratios remain ratios"
    )


@pytest.mark.parametrize("mutation", ["body", "cell", "overhead"])
def test_capacity_refuses_complete_payload_or_identity_overhead_without_truncation(mutation):
    package = workbook_package()
    original = deepcopy(package["report_data"])
    if mutation == "body":
        package["render_context"]["oversized"] = "x" * 8_388_608
    elif mutation == "cell":
        package["report_data"]["tables"][0]["rows"][0]["cells"]["month"]["canonical_value"] = (
            "x" * 32768
        )
    else:
        member = package["report_data"]["tables"][1]["rows"][0]
        package["report_data"]["tables"][1]["rows"] = [
            {**deepcopy(member), "row_id": f"capacity-{index}"} for index in range(7000)
        ]
        original = deepcopy(package["report_data"])
    with pytest.raises(ValueError):
        preflight_eligibility_package(package)
    if mutation != "cell":
        assert package["report_data"] == original


def test_valid_large_identity_within_measured_policy_is_not_refused():
    package = workbook_package()
    package["render_context"]["large_identity"] = "x" * 6_000_000
    totals = preflight_eligibility_package(package)
    assert totals["request_body_bytes"] < 8_388_608
    assert totals["total_text_bytes"] < 16_777_216


@pytest.mark.parametrize("kind", ["evaluated", "published"])
def test_frozen_schema_accepts_actual_producer_and_matches_committed_contract(kind):
    selection, months = evaluated_example() if kind == "evaluated" else published_example()
    schema = composite_eligibility_report_schema()
    Draft202012Validator(schema).validate(build_eligibility_dataset(selection, months))
    root = Path(__file__).resolve().parents[3]
    assert json.loads((root / "contracts/composite_review.v4.schema.json").read_text()) == schema


def test_eligibility_cannot_be_injected_as_twr_or_linked_options():
    selection, _ = evaluated_example()
    with pytest.raises(ValueError):
        CompositeReviewJobRequest.model_validate(
            {
                "eligibility_selection": selection.model_dump(mode="json"),
                "options": {"composite_eligibility_selection": {}},
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "oversized", "retry", "compressed"])
async def test_actual_httpx_bounded_read_and_retry_transport(monkeypatch, mode):
    import gzip

    from app.clients.http_resilience import bounded_read_with_retry

    requests = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if mode == "retry" and len(requests) == 1:
            return httpx.Response(503, json={"detail": "retry"})
        if mode == "compressed":
            return httpx.Response(
                200, content=gzip.compress(b'{"ok":true}'), headers={"Content-Encoding": "gzip"}
            )
        return httpx.Response(200, json={"value": "x" * (300 if mode == "oversized" else 3)})

    monkeypatch.setattr(
        "app.clients.http_resilience.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    arguments = {
        "url": "http://manage/api/v1/rebalance/composites/test/definitions/d/monthly-eligibility/evaluations/r1",
        "timeout_seconds": 1,
        "headers": {"X-Tenant-Id": "tenant"},
        "json_body": None,
        "max_response_bytes": 100,
        "max_retries": 1,
        "backoff_seconds": 0,
    }
    if mode in {"oversized", "compressed"}:
        with pytest.raises(ValueError, match="UPSTREAM_"):
            await bounded_read_with_retry(**arguments)
    else:
        status, payload = await bounded_read_with_retry(**arguments)
        assert status == 200 and payload == {"value": "xxx"}
    assert requests[0].headers["Accept-Encoding"] == "identity"
    assert len(requests) == (2 if mode == "retry" else 1)


@pytest.mark.asyncio
async def test_xlsx_without_exact_v4_capability_is_refused_before_job_or_source(
    tmp_path, monkeypatch
):
    from app.report_ordering_catalogue.service import ReportOrderingCatalogueService

    selected, _ = evaluated_example()
    calls = []

    async def unavailable(self, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(state="unavailable")

    monkeypatch.setattr(
        ReportOrderingCatalogueService, "document_contract_supportability", unavailable
    )
    with composite_lifecycle(tmp_path, monkeypatch) as (ledger, store, worker, supplier):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews",
                json={
                    "eligibility_selection": selected.model_dump(mode="json"),
                    "requested_output_formats": ["xlsx"],
                },
                headers={**HEADERS, "X-Tenant-Id": selected.tenant_id},
            )
        assert response.status_code == 503
        assert calls == [
            {
                "report_type": "composite_review",
                "format_id": "xlsx",
                "contract_version": "composite_review.v4",
                "template_version": "v4",
            }
        ]
        assert supplier["calls"] == []


@pytest.mark.asyncio
async def test_v4_worker_package_and_retained_rerender_bind_actual_manage_snapshot(
    tmp_path, monkeypatch
):
    from app.report_ordering_catalogue.service import ReportOrderingCatalogueService
    from app.reporting_render.package_builder import _build_render_package
    from tests.unit.composite_reporting.test_registered_lifecycle import ControlledRenderBoundary

    selection, months = evaluated_example()
    source_calls = []

    async def source(**kwargs):
        source_calls.append(kwargs)
        return 200, deepcopy(months[0]["proposal"])

    async def ready(self, **kwargs):
        return SimpleNamespace(state="ready")

    monkeypatch.setattr(ReportOrderingCatalogueService, "document_contract_supportability", ready)
    monkeypatch.setattr(settings, "manage_read_actor_id", "configured-reader")
    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", source)
    boundary = ControlledRenderBoundary()  # real package emission, deliberately no workbook claim
    with composite_lifecycle(tmp_path, monkeypatch, render_client=boundary) as (
        ledger,
        store,
        worker,
        performance,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            ordered = await client.post(
                "/reports/composite-reviews",
                json={
                    "eligibility_selection": selection.model_dump(mode="json"),
                    "requested_output_formats": ["xlsx"],
                },
                headers={**HEADERS, "X-Tenant-Id": selection.tenant_id},
            )
            assert ordered.status_code == 202, ordered.text
            job_id = ordered.json()["report_job_id"]
            await worker.run_once(worker_id="v4-package-worker", max_items=1, lease_seconds=30)
        assert len(boundary.packages) == 1, ledger.get_job(job_id).failure_message
        package = boundary.packages[0]
        assert package["template_version"] == "v4"
        assert package["report_data_contract_version"] == "composite_review.v4"
        identity = package["render_context"]["archive"]["composite_report_identity"]
        assert identity["selection"] == selection.model_dump(mode="json")
        assert identity["qualification"] == "CONTROLLED_ELIGIBILITY_SOURCE_REPLAY"
        assert "calculation_id" not in identity["selection"]
        record = store.get_snapshot_by_job(job_id)
        job = ledger.get_job(job_id)
        source_calls_before = len(source_calls)
        rebuilt = _build_render_package(
            job=job,
            snapshot=record.snapshot_payload,
            render_job_id="test-retained-rerender",
            snapshot_id=record.snapshot_id,
            report_revision_id=record.report_revision_id,
            snapshot_record=record,
        )
        assert rebuilt["report_data"] == package["report_data"]
        assert rebuilt["render_context"] == package["render_context"]
        assert len(source_calls) == source_calls_before == 1
        assert performance["calls"] == []
        for change in ({"source_revision_vector": {}}, {"report_revision_id": "foreign"}):
            invalid = record.model_copy(update=change)
            with pytest.raises(ValueError):
                _build_render_package(
                    job=job,
                    snapshot=invalid.snapshot_payload,
                    render_job_id="test-bad",
                    snapshot_id=invalid.snapshot_id,
                    report_revision_id=invalid.report_revision_id,
                    snapshot_record=invalid,
                )
