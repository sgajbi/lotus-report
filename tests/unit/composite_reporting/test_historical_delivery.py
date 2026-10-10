"""Registered Report API and durable SQLite capture with controlled Manage inputs."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.config import settings
from app.report_ordering_catalogue.service import ReportOrderingCatalogueService
from app.reporting_render.package_builder import _build_render_package
from tests.unit.composite_reporting.historical_examples import graph_example
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    ControlledRenderBoundary,
    app,
    composite_lifecycle,
)


async def exercise_historical_delivery(
    tmp_path, monkeypatch, definition, published, *, adapters=None, capacity=False
):
    calls, capabilities, retained = [], [], []
    state = {}

    async def source(**kwargs):
        calls.append(deepcopy(kwargs))
        month, path, binding = state["month"], kwargs["url"], kwargs["json_body"]
        if binding is not None:
            receipts = [*month["lineage_receipts"], *([month["receipt"]] if published else [])]
            result = next(
                row for row in receipts if row["approval"]["content_hash"] == binding["digest"]
            )
            assert result["product_version"] == binding["product_version"]
        elif "/evaluations/" in path:
            result = month["proposal"]
        elif "/publications/" in path:
            result = next(
                row
                for row in [month["publication"], month["parent_publication"]]
                if row is not None and str(row["sequence"]) == path.rsplit("/", 1)[1]
            )
        elif "/universe-attestations/" in path:
            result = month["universe"]
        else:
            result = next(
                row
                for row in [month["membership"], month["parent_membership"]]
                if path.endswith("/" + row["membership_revision"])
            )
        return 200, deepcopy(result)

    async def support(self, **kwargs):
        capabilities.append(kwargs)
        return SimpleNamespace(state="ready")

    monkeypatch.setattr(settings, "composite_historical_policy_enabled", True)
    monkeypatch.setattr(settings, "manage_read_actor_id", "configured-report-reader")
    monkeypatch.setattr(settings, "manage_read_service_identity", "configured-report-service")
    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", source)
    monkeypatch.setattr(ReportOrderingCatalogueService, "document_contract_supportability", support)
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
            for step in ("root", "correction-2", "correction-3"):
                selection, months = graph_example(definition, step, published)
                state["month"] = months[0]
                request = {
                    "eligibility_selection": selection.model_dump(mode="json"),
                    "requested_output_formats": ["xlsx"],
                }
                headers = {
                    **HEADERS,
                    "X-Tenant-Id": selection.tenant_id,
                    "Idempotency-Key": uuid4().hex,
                }
                ordered = await client.post(
                    "/reports/composite-reviews", headers=headers, json=request
                )
                assert ordered.status_code == 202, ordered.text
                job_id = ordered.json()["report_job_id"]
                await worker.run_once(
                    worker_id="historical-policy-delivery", max_items=1, lease_seconds=30
                )
                job, record = ledger.get_job(job_id), store.get_snapshot_by_job(job_id)
                if capacity:
                    assert not boundary.packages and job.archive_document_id is None
                    assert "COMPOSITE_HISTORICAL_WORKBOOK_CAPACITY_EXCEEDED" in job.failure_message
                    assert record.snapshot_payload["contract_version"] == "composite_review.v7"
                    continue
                assert len(boundary.packages) == len(retained) + 1, job.failure_message
                package = boundary.packages[-1]
                assert package["report_data"] == record.snapshot_payload
                assert record.snapshot_payload["source_months"] == months
                assert package["report_data_contract_version"] == "composite_review.v7"
                assert package["template_version"] == "v7"
                from app.composite_reporting.historical_tables import CALCULATION_BOUNDARY

                assert (
                    package["render_context"]["archive"]["composite_report_identity"][
                        "calculation_boundary"
                    ]
                    == CALCULATION_BOUNDARY
                )
                assert package["render_context"]["archive"]["composite_report_identity"][
                    "selection"
                ] == selection.model_dump(mode="json")
                before = len(calls)
                replay = _build_render_package(
                    job=job,
                    snapshot=record.snapshot_payload,
                    render_job_id="retained-historical",
                    snapshot_id=record.snapshot_id,
                    report_revision_id=record.report_revision_id,
                    snapshot_record=record,
                )
                assert replay["report_data"] == package["report_data"]
                assert replay["render_context"] == package["render_context"]
                retry = await client.post(
                    "/reports/composite-reviews", headers=headers, json=request
                )
                assert retry.json()["report_job_id"] == job_id and len(calls) == before
                foreign = {**headers, "X-Tenant-Id": "foreign"}
                assert (
                    await client.get(f"/reports/jobs/{job_id}/snapshot", headers=foreign)
                ).status_code == 404
                assert (
                    await client.post("/reports/composite-reviews", headers=foreign, json=request)
                ).status_code == 400
                retained.append((job_id, record.model_dump(mode="json"), deepcopy(package)))
            assert not performance["calls"]
            if capacity:
                return []
            assert len({record["report_revision_id"] for _, record, _ in retained}) == 3
            assert all(
                call
                == {
                    "report_type": "composite_review",
                    "format_id": "xlsx",
                    "contract_version": "composite_review.v7",
                    "template_version": "v7",
                }
                for call in capabilities
            )
    return retained


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", ["v1", "v2"])
@pytest.mark.parametrize("published", [False, True])
async def test_historical_registered_capture_and_retained_replay(
    tmp_path, monkeypatch, definition, published
):
    await exercise_historical_delivery(tmp_path, monkeypatch, definition, published)


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
async def test_v7_capacity_refuses_before_render_io(tmp_path, monkeypatch, limit):
    from app.composite_reporting.eligibility_tables import CAPACITY_POLICY

    monkeypatch.setitem(CAPACITY_POLICY, limit, 1)
    assert not await exercise_historical_delivery(tmp_path, monkeypatch, "v2", True, capacity=True)
