"""Actual Report package emission; controlled Manage and declining Render transport."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.composite_reporting.admission import response_digest
from app.composite_reporting.amendment_tables import build_amendment_dataset
from app.composite_reporting.eligibility_tables import CAPACITY_POLICY
from app.composite_reporting.models import AmendmentEligibilitySelection
from app.config import settings
from app.report_ordering_catalogue.service import ReportOrderingCatalogueService
from app.reporting_render.package_builder import _build_render_package
from tests.unit.composite_reporting.amendment_examples import example
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    ControlledRenderBoundary,
    app,
    composite_lifecycle,
)


def two_month_example():
    root = next(
        parent for parent in Path(__file__).resolve().parents if (parent / "contracts").is_dir()
    )
    fixture = root / "tests/fixtures/composite-amendment"
    source = json.loads((fixture / "two-month-controlled-source.json").read_text())
    request = json.loads((fixture / "two-month-request.json").read_text())
    selection = AmendmentEligibilitySelection.model_validate(request["eligibility_selection"])
    months = source["months"]
    for month in months:
        month["response_digests"] = {
            key: response_digest(value) for key, value in month.items() if isinstance(value, dict)
        }
    build_amendment_dataset(selection, months)
    return selection, months


async def exercise_package(
    tmp_path, monkeypatch, cases, *, ready=True, corrupt=False, capacity=False, adapters=None
):
    capabilities, source_calls, retained = [], [], []
    state = {}

    async def support(self, **kwargs):
        capabilities.append(kwargs)
        return SimpleNamespace(state="ready" if ready else "unavailable")

    async def source(**kwargs):
        source_calls.append(deepcopy(kwargs))
        months = state["months"]
        binding = kwargs["json_body"]
        if binding is not None:
            receipts = [
                item
                for month in months
                for item in [month.get("receipt"), *month["lineage_receipts"]]
                if item
            ]
            product = next(
                item for item in receipts if item["approval"]["content_hash"] == binding["digest"]
            )
        elif "/monthly-eligibility/evaluations/" in kwargs["url"]:
            product = next(
                month["proposal"]
                for month in months
                if kwargs["url"].endswith("/" + month["proposal"]["evaluation_revision"])
            )
        elif "/publications/" in kwargs["url"]:
            sequence = int(kwargs["url"].rsplit("/", 1)[1])
            product = next(
                item
                for month in months
                for item in [month["publication"], month["parent_publication"]]
                if item["sequence"] == sequence
            )
        elif "/universe-attestations/" in kwargs["url"]:
            product = next(
                month["universe"]
                for month in months
                if kwargs["url"].endswith("/" + month["universe"]["attestation_version"])
            )
        else:
            product = next(
                item
                for month in months
                for item in [month["membership"], month["parent_membership"]]
                if kwargs["url"].endswith("/" + item["membership_revision"])
            )
        product = deepcopy(product)
        if corrupt and binding is not None:
            product["lineage"] = {"tampered": True}
        return 200, product

    monkeypatch.setattr(ReportOrderingCatalogueService, "document_contract_supportability", support)
    monkeypatch.setattr(settings, "manage_read_actor_id", "configured-reader")
    monkeypatch.setattr(settings, "manage_read_service_identity", "configured-report-service")
    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", source)
    boundary = ControlledRenderBoundary()
    with composite_lifecycle(tmp_path, monkeypatch, render_client=boundary, adapters=adapters) as (
        ledger,
        store,
        worker,
        performance,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            for selection, months in cases:
                state["months"] = months
                headers = {
                    **HEADERS,
                    "X-Tenant-Id": selection.tenant_id,
                    "Idempotency-Key": uuid4().hex,
                }
                request = {
                    "eligibility_selection": selection.model_dump(mode="json"),
                    "requested_output_formats": ["xlsx"],
                }
                ordered = await client.post(
                    "/reports/composite-reviews", headers=headers, json=request
                )
                if not ready:
                    assert ordered.status_code == 503 and not source_calls and not boundary.packages
                    continue
                assert ordered.status_code == 202, ordered.text
                job_id = ordered.json()["report_job_id"]
                await worker.run_once(worker_id="amendment-package", max_items=1, lease_seconds=30)
                job = ledger.get_job(job_id)
                record = store.get_snapshot_by_job(job_id)
                if corrupt:
                    assert not boundary.packages and job.archive_document_id is None
                    assert record.snapshot_payload["capture_status"] == "failed"
                    continue
                if capacity:
                    assert not boundary.packages and job.archive_document_id is None
                    assert "COMPOSITE_AMENDMENT_WORKBOOK_CAPACITY_EXCEEDED" in job.failure_message
                    assert record.snapshot_payload["contract_version"] == "composite_review.v6"
                    continue
                package = boundary.packages[-1]
                assert len(boundary.packages) == len(retained) + 1, job.failure_message
                assert package["report_data"] == record.snapshot_payload
                assert record.snapshot_payload == build_amendment_dataset(selection, months)
                assert package["report_data_contract_version"] == "composite_review.v6"
                assert (
                    package["template_id"] == "composite-review"
                    and package["template_version"] == "v6"
                )
                archive = package["render_context"]["archive"]
                assert (
                    archive["composite_id"] == selection.composite_id
                    and archive["portfolio_id"] is None
                )
                assert archive["report_revision_id"] == record.report_revision_id
                assert archive["composite_report_identity"]["selection"] == selection.model_dump(
                    mode="json"
                )
                assert (
                    archive["composite_report_identity"]["qualification"]
                    == "CONTROLLED_MONTHLY_SOURCE_AMENDMENT_REPLAY"
                )
                assert {
                    item["source_service"] for item in record.source_revision_vector["revisions"]
                } == {"lotus-manage"}
                before = len(source_calls)
                replay = _build_render_package(
                    job=job,
                    snapshot=record.snapshot_payload,
                    render_job_id="retained-replay",
                    snapshot_id=record.snapshot_id,
                    report_revision_id=record.report_revision_id,
                    snapshot_record=record,
                )
                assert (
                    replay["report_data"] == package["report_data"]
                    and replay["render_context"] == package["render_context"]
                )
                assert len(source_calls) == before
                retry = await client.post(
                    "/reports/composite-reviews", headers=headers, json=request
                )
                assert retry.json()["report_job_id"] == job_id
                assert len(source_calls) == before
                for change in (
                    {"source_revision_vector": {}},
                    {"report_revision_id": "foreign"},
                    {"report_data_contract_version": "composite_review.v4"},
                ):
                    bad = record.model_copy(update=change)
                    with pytest.raises(ValueError):
                        _build_render_package(
                            job=job,
                            snapshot=bad.snapshot_payload,
                            render_job_id="bad-replay",
                            snapshot_id=bad.snapshot_id,
                            report_revision_id=bad.report_revision_id,
                            snapshot_record=bad,
                        )
                retained.append((job_id, record.model_dump(mode="json"), deepcopy(package)))
            assert performance["calls"] == []
            for job_id, record, package in retained:
                assert store.get_snapshot_by_job(job_id).model_dump(mode="json") == record
                assert package["report_data"] == record["snapshot_payload"]
        assert all(
            call
            == {
                "report_type": "composite_review",
                "format_id": "xlsx",
                "contract_version": "composite_review.v6",
                "template_version": "v6",
            }
            for call in capabilities
        )
    return retained


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("evaluated", [False, True])
async def test_original_and_corrected_worker_packages_use_same_retained_dataset(
    tmp_path, monkeypatch, version, evaluated
):
    retained = await exercise_package(
        tmp_path, monkeypatch, [example(version, revision, evaluated) for revision in (2, 3)]
    )
    assert (
        len(retained) == 2
        and retained[0][1]["report_revision_id"] != retained[1][1]["report_revision_id"]
    )


@pytest.mark.asyncio
async def test_two_month_package_keeps_global_row_ordinals(tmp_path, monkeypatch):
    retained = await exercise_package(tmp_path, monkeypatch, [two_month_example()])
    rows = next(
        table["rows"]
        for table in retained[0][2]["report_data"]["tables"]
        if table["table_id"] == "Amendments"
    )
    assert rows[0]["row_id"] == "m0:a0" and rows[34]["row_id"] == "m1:a34"


@pytest.mark.asyncio
@pytest.mark.parametrize("ready,corrupt", [(False, False), (True, True)])
async def test_unavailable_capability_or_invalid_source_never_reaches_render(
    tmp_path, monkeypatch, ready, corrupt
):
    assert not await exercise_package(
        tmp_path, monkeypatch, [example()], ready=ready, corrupt=corrupt
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit",
    [
        "max_request_body_bytes",
        "max_total_cells",
        "max_total_rows",
        "max_total_text_bytes",
        "max_sheets",
    ],
)
async def test_full_package_capacity_refuses_before_render_io(tmp_path, monkeypatch, limit):
    # Same valid package that passes production bounds above; a small policy bound
    # exercises each whole-workbook/overhead guard without allocating huge fixtures.
    monkeypatch.setitem(CAPACITY_POLICY, limit, 1)
    assert not await exercise_package(tmp_path, monkeypatch, [two_month_example()], capacity=True)
