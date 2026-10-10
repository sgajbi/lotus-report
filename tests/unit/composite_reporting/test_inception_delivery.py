"""Registered SQLite custody and package proof; no actual v8 XLSX/Archive claim."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from app.config import settings
from app.report_ordering_catalogue.service import ReportOrderingCatalogueService
from app.reporting_render.package_builder import _build_render_package
from tests.unit.composite_reporting.inception_examples import inception_example
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    ControlledRenderBoundary,
    app,
    composite_lifecycle,
)


async def exercise_inception_delivery(
    tmp_path, monkeypatch, *, inception="2026-01-01", history=False, corrected=False
):
    selection, source, definition = inception_example(inception)
    context = {"definition": definition.pin.model_dump(mode="json"), "since_inception": True}
    memberships = []
    if history:
        from tests.unit.composite_reporting.supplied_history_examples import (
            supplied_history_example,
        )

        selection, source, definition, selected_context, memberships = supplied_history_example(
            corrected, include_si=True
        )
        context = selected_context.model_dump(mode="json")
    boundary, manage_calls, capabilities = ControlledRenderBoundary(), [], []
    state = {"definitions": {"d1": deepcopy(definition.source_response)}, "offline": False}
    state["definitions"].update(
        {item.pin.membership_revision: item.source_response for item in memberships}
    )

    async def manage_source(**kwargs):
        manage_calls.append(deepcopy(kwargs))
        if state["offline"]:
            pytest.fail("Retained package must not refetch definition")
        version = kwargs["url"].rsplit("/", 1)[1]
        return 200, deepcopy(state["definitions"][version])

    async def support(self, **kwargs):
        capabilities.append(kwargs)
        return SimpleNamespace(state="ready")

    monkeypatch.setattr(settings, "manage_read_actor_id", "configured-report-reader")
    monkeypatch.setattr(settings, "manage_read_service_identity", "configured-report-service")
    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", manage_source)
    monkeypatch.setattr(ReportOrderingCatalogueService, "document_contract_supportability", support)
    with composite_lifecycle(tmp_path, monkeypatch, render_client=boundary) as stack:
        ledger, store, worker, supplier = stack
        supplier["response"] = source
        request = {
            "selection": selection.model_dump(mode="json"),
            "source_context": context,
            "requested_output_formats": ["xlsx"],
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            ordered = await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
            assert ordered.status_code == 202, ordered.text
            job_id = ordered.json()["report_job_id"]
            assert not manage_calls and not supplier["calls"]
            # A newer source version exists before capture. Exact d1 still wins.
            state["definitions"]["d2"] = inception_example("2025-12-01", "d2")[2].source_response
            await worker.run_once(worker_id="inception-worker", max_items=1, lease_seconds=30)
            job, record = ledger.get_job(job_id), store.get_snapshot_by_job(job_id)
            assert len(boundary.packages) == 1, job.failure_message
            package = boundary.packages[0]
            assert package["report_data"] == record.snapshot_payload
            assert package["report_data_contract_version"] == "composite_review.v8"
            assert package["template_version"] == "v8"
            assert (
                record.snapshot_payload["definition"]["source_response"]
                == definition.source_response
            )
            assert record.snapshot_payload["source_response"] == source
            assert {r["source_service"] for r in record.source_revision_vector["revisions"]} == {
                "lotus-manage",
                "lotus-performance",
            }
            assert len(manage_calls) == 1 + len(memberships)
            assert manage_calls[0]["url"].endswith("/definitions/d1")
            assert manage_calls[0]["headers"]["X-Tenant-Id"] == selection.tenant_id
            assert manage_calls[0]["headers"]["X-Capabilities"] == "manage.read"
            assert len(supplier["calls"]) == 1
            assert record.snapshot_payload["supplied_memberships"] == [
                item.model_dump(mode="json") for item in memberships
            ]
            state["offline"] = True
            supplier["response"] = {}
            replay = _build_render_package(
                job=job,
                snapshot=record.snapshot_payload,
                render_job_id="retained-inception",
                snapshot_id=record.snapshot_id,
                report_revision_id=record.report_revision_id,
                snapshot_record=record,
            )
            assert replay["report_data"] == package["report_data"]
            assert replay["render_context"] == package["render_context"]
            retry = await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
            assert retry.json()["report_job_id"] == job_id
            assert len(manage_calls) == 1 + len(memberships) and len(supplier["calls"]) == 1
            assert (
                await client.get(
                    f"/reports/jobs/{job_id}/snapshot",
                    headers={**HEADERS, "X-Tenant-Id": "foreign"},
                )
            ).status_code == 404
            assert (
                capabilities
                == [
                    {
                        "report_type": "composite_review",
                        "format_id": "xlsx",
                        "contract_version": "composite_review.v8",
                        "template_version": "v8",
                    }
                ]
                * 2
            )
            return deepcopy(package)


@pytest.mark.asyncio
@pytest.mark.parametrize("inception", ["2026-01-01", "2025-12-31"])
async def test_registered_definition_vector_capture_and_source_off_package_replay(
    tmp_path, monkeypatch, inception
):
    await exercise_inception_delivery(tmp_path, monkeypatch, inception=inception)


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected", [False, True])
async def test_registered_supplied_history_and_si_share_definition_custody(
    tmp_path, monkeypatch, corrected
):
    package = await exercise_inception_delivery(
        tmp_path, monkeypatch, history=True, corrected=corrected
    )
    assert package["render_context"]["archive"]["composite_report_identity"]["source_context"][
        "memberships"
    ]


@pytest.mark.asyncio
async def test_missing_definition_fails_without_empty_or_zero_si(tmp_path, monkeypatch):
    selection, source, definition = inception_example()

    async def missing(**kwargs):
        return 404, {"detail": "missing exact original definition"}

    monkeypatch.setattr(settings, "manage_read_actor_id", "configured-report-reader")
    monkeypatch.setattr(settings, "manage_read_service_identity", "configured-report-service")
    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", missing)
    with composite_lifecycle(tmp_path, monkeypatch) as (ledger, store, worker, supplier):
        supplier["response"] = source
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            ordered = await client.post(
                "/reports/composite-reviews",
                headers=HEADERS,
                json={
                    "selection": selection.model_dump(mode="json"),
                    "source_context": {
                        "definition": definition.pin.model_dump(mode="json"),
                        "since_inception": True,
                    },
                },
            )
            assert ordered.status_code == 202, ordered.text
            job_id = ordered.json()["report_job_id"]
            await worker.run_once(worker_id="missing-definition", max_items=1, lease_seconds=30)
            assert ledger.get_job(job_id).status == "failed"
            record = store.get_snapshot_by_job(job_id)
            assert record.report_revision_id is None
            assert record.snapshot_payload["capture_status"] == "failed"
            assert "source_response" not in record.snapshot_payload
            assert "definition" not in record.snapshot_payload


@pytest.mark.asyncio
async def test_unavailable_exact_v8_renderer_refuses_before_order_or_source_io(
    tmp_path, monkeypatch
):
    selection, _, definition = inception_example()

    async def unsupported(self, **kwargs):
        assert kwargs["contract_version"] == "composite_review.v8"
        assert kwargs["template_version"] == "v8"
        return SimpleNamespace(state="unavailable")

    monkeypatch.setattr(
        ReportOrderingCatalogueService, "document_contract_supportability", unsupported
    )
    with composite_lifecycle(tmp_path, monkeypatch) as (_, _, _, supplier):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            result = await client.post(
                "/reports/composite-reviews",
                headers=HEADERS,
                json={
                    "selection": selection.model_dump(mode="json"),
                    "source_context": {
                        "definition": definition.pin.model_dump(mode="json"),
                        "since_inception": True,
                    },
                    "requested_output_formats": ["xlsx"],
                },
            )
            assert result.status_code == 503
            assert result.json()["detail"]["code"] == "composite_product_render_unavailable"
            assert not supplier["calls"]
