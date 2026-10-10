"""One immutable v8 manifest reuses all v2 return products and supplied history."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import jsonschema
import pytest

from app.composite_reporting.admission import admit_composite_response
from app.composite_reporting.models import CompositeReviewJobRequest
from app.composite_reporting.product_contract import (
    CapturedReturnProduct,
    validate_composite_dataset,
)
from app.composite_reporting.product_tables import build_product_tables
from app.composite_reporting.source_context_contract import (
    project_source_context,
    source_context_report_schema,
)
from app.composite_reporting.table_builder import build_composite_tables
from app.config import settings
from app.reporting_render.package_builder import _build_render_package
from tests.unit.composite_reporting.cohesive_examples import cohesive_example
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    ControlledRenderBoundary,
    app,
    composite_lifecycle,
)


def cohesive_dataset(corrected=False):
    raw_request, sources, definition, memberships = cohesive_example(corrected)
    request = CompositeReviewJobRequest.model_validate(raw_request)
    primary = admit_composite_response(
        selection=request.selection,
        admitted_tenant_id="tenant-a",
        status_code=200,
        payload=sources[str(request.selection.calculation_id)],
    )
    products = [
        CapturedReturnProduct(
            pin=pin,
            endpoint="/composites/twr",
            method="POST",
            source_response_digest=pin.selection.response_digest,
            source_response=sources[str(pin.selection.calculation_id)],
        )
        for pin in request.source_products
    ]
    return project_source_context(
        build_product_tables(build_composite_tables(primary), products),
        request.source_context,
        definition,
        memberships,
    )


@pytest.mark.parametrize("corrected", [False, True])
def test_published_cohesive_caller_recipe_matches_all_exact_pins(corrected):
    name = "corrected" if corrected else "original"
    examples = Path(__file__).resolve().parents[3] / "contracts" / "examples"
    request = json.loads(
        (examples / f"composite-review.v8.cohesive-{name}.request.json").read_text(encoding="utf-8")
    )
    assert request == cohesive_example(corrected)[0]
    CompositeReviewJobRequest.model_validate(request)
    jsonschema.validate(cohesive_dataset(corrected), source_context_report_schema())


async def exercise_cohesive_delivery(tmp_path, monkeypatch):
    from app.report_ordering_catalogue.service import ReportOrderingCatalogueService

    boundary, performance_calls, manage_calls, packages, requests = (
        ControlledRenderBoundary(),
        [],
        [],
        [],
        [],
    )
    state = {"sources": {}, "manage": {}, "offline": False}

    async def performance(**kwargs):
        assert not state["offline"], "Replay must not read Performance"
        performance_calls.append(deepcopy(kwargs))
        return 200, deepcopy(state["sources"][kwargs["json_body"]["calculation_id"]])

    async def manage(**kwargs):
        assert not state["offline"], "Replay must not read Manage"
        manage_calls.append(deepcopy(kwargs))
        return 200, deepcopy(state["manage"][kwargs["url"].rsplit("/", 1)[1]])

    async def support(self, **kwargs):
        assert kwargs["contract_version"] == "composite_review.v8"
        assert kwargs["template_version"] == "v8"
        return SimpleNamespace(state="ready")

    monkeypatch.setattr(settings, "manage_read_actor_id", "configured-report-reader")
    monkeypatch.setattr(settings, "manage_read_service_identity", "configured-report-service")
    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", manage)
    monkeypatch.setattr(ReportOrderingCatalogueService, "document_contract_supportability", support)
    with composite_lifecycle(tmp_path, monkeypatch, render_client=boundary) as (
        ledger,
        store,
        worker,
        _,
    ):
        monkeypatch.setattr("app.clients.performance_client.post_with_retry", performance)
        retained = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            for corrected in [False, True]:
                request, sources, definition, memberships = cohesive_example(corrected)
                requests.append(request)
                state["sources"] = sources
                state["manage"] = {
                    "d1": definition.source_response,
                    **{m.pin.membership_revision: m.source_response for m in memberships},
                }
                headers = {**HEADERS, "Idempotency-Key": f"cohesive-{corrected}"}
                ordered = await client.post(
                    "/reports/composite-reviews", json=request, headers=headers
                )
                assert ordered.status_code == 202, ordered.text
                job_id = ordered.json()["report_job_id"]
                await worker.run_once(worker_id="cohesive-worker", max_items=1, lease_seconds=30)
                job, record = ledger.get_job(job_id), store.get_snapshot_by_job(job_id)
                assert len(boundary.packages) == len(requests), job.failure_message
                package = boundary.packages[-1]
                assert package["report_data"] == record.snapshot_payload
                assert record.snapshot_payload == cohesive_dataset(corrected)
                assert package["report_data_contract_version"] == "composite_review.v8"
                assert package["template_version"] == "v8"
                assert {
                    r["calculation_run_id"]
                    for r in record.source_revision_vector["revisions"]
                    if r["source_service"] == "lotus-performance"
                } == set(sources)
                assert len(store.list_upstream_calls(record.snapshot_id)) == 4 + len(memberships)
                identity = package["render_context"]["archive"]["composite_report_identity"]
                assert [p["pin"] for p in identity["source_products"]] == request["source_products"]
                assert identity["source_context"] == request["source_context"]
                assert (
                    await client.post("/reports/composite-reviews", json=request, headers=headers)
                ).json()["report_job_id"] == job_id
                retained.append((job, record))
                packages.append(deepcopy(package))
            assert retained[0][1].report_revision_id != retained[1][1].report_revision_id
            assert (
                packages[0]["report_data"]["source_products"][1]
                == packages[1]["report_data"]["source_products"][1]
            )
            state["offline"] = True
            state["sources"], state["manage"] = {}, {}
            for ordinal, (job, record) in enumerate(retained):
                replay = _build_render_package(
                    job=job,
                    snapshot=record.snapshot_payload,
                    render_job_id=f"cohesive-retained-{ordinal}",
                    snapshot_id=record.snapshot_id,
                    report_revision_id=record.report_revision_id,
                    snapshot_record=record,
                )
                assert replay["report_data"] == packages[ordinal]["report_data"]
                assert replay["render_context"] == packages[ordinal]["render_context"]
                assert (
                    await client.get(
                        f"/reports/jobs/{job.job_id}/snapshot",
                        headers={**HEADERS, "X-Tenant-Id": "foreign"},
                    )
                ).status_code == 404
            assert len(performance_calls) == 6 and len(manage_calls) == 5
            assert [call["json_body"] for call in performance_calls] == [
                selection.performance_request()
                for raw_request in requests
                for request in [CompositeReviewJobRequest.model_validate(raw_request)]
                for selection in [
                    request.selection,
                    *(p.selection for p in request.source_products),
                ]
            ]
            return {
                "requests": requests,
                "packages": packages,
                "source_off_replay_equal": True,
                "performance_calls": 6,
                "manage_calls": 5,
            }


@pytest.mark.asyncio
async def test_registered_cohesive_original_correction_and_both_retained_replays(
    tmp_path, monkeypatch
):
    result = await exercise_cohesive_delivery(tmp_path, monkeypatch)
    for corrected, package in zip([False, True], result["packages"], strict=True):
        tables = {t["table_id"]: t for t in package["report_data"]["tables"]}
        assert len(tables["MonthlyReturns"]["rows"]) == 12
        assert {"AnnualReturns", "TrailingReturns", "SinceInception", "MembershipHistory"}.issubset(
            tables
        )
        assert tables["SinceInception"]["rows"][0]["cells"]["return"]["canonical_value"] == (
            "0.020000000000" if corrected else "0.010000000000"
        )
        assert tables["AnnualReturns"]["rows"][0]["cells"]["return"]["canonical_value"] == (
            "0.020000000000" if corrected else "0.010000000000"
        )
        assert (
            tables["TrailingReturns"]["rows"][0]["cells"]["return"]["canonical_value"]
            == "0.000000000000"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "product_hash",
        "product_digest",
        "missing_period",
        "missing_membership",
        "definition",
        "forge_return",
        "omit_product",
        "pointer",
        "unit",
    ],
)
def test_cohesive_all_vector_custody_and_projection_refuse_corruption(mutation):
    data = cohesive_dataset(True)
    if mutation == "missing_membership":
        data["supplied_memberships"].pop()
        data["source_context"]["memberships"].pop()
    elif mutation == "omit_product":
        data["source_products"].pop()
    elif mutation == "product_hash":
        data["source_products"][1]["pin"]["selection"]["windows"][0]["membership_content_hash"] = (
            "sha256:" + "f" * 64
        )
    elif mutation == "definition":
        data["source_products"][1]["pin"]["selection"]["windows"][0]["definition_content_hash"] = (
            "sha256:" + "f" * 64
        )
    elif mutation == "product_digest":
        data["source_products"][0]["source_response_digest"] = "sha256:" + "f" * 64
    elif mutation == "missing_period":
        data["source_products"][0]["source_response"]["periods"].pop()
    else:
        table = next(t for t in data["tables"] if t["table_id"] == "TrailingReturns")
        if mutation == "forge_return":
            table["rows"][0]["cells"]["return"]["canonical_value"] = "1.99"
        elif mutation == "pointer":
            table["rows"][0]["cells"]["return"]["source_pointer"] = (
                "/source_response/cumulative_return"
            )
        else:
            next(c for c in table["columns"] if c["column_id"] == "return")["display_unit"] = (
                "PERCENTAGE_POINTS"
            )
    with pytest.raises(ValueError):
        validate_composite_dataset(data)


def test_all_product_raw_responses_share_the_finite_capture_budget():
    from app.composite_reporting.admission import response_digest

    data = cohesive_dataset()
    for product in data["source_products"]:
        product["source_response"]["controlled_evidence"] = "x" * 4_200_000
        digest = response_digest(product["source_response"])
        product["source_response_digest"] = digest
        product["pin"]["selection"]["response_digest"] = digest
    with pytest.raises(ValueError, match="CAPTURE_CAPACITY_EXCEEDED"):
        validate_composite_dataset(data)
