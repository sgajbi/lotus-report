"""Configured transport and registered lifecycle with a named controlled supplier.

This is unit-level ASGI/SQLite proof, not live Performance/IAM or XLSX acceptance.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from app.clients.performance_client import PerformanceClient
from app.composite_reporting.eligibility_tables import eligibility_workbook_projection
from app.composite_reporting.source_capture import CompositeInputProvider
from app.config import settings
from app.report_ordering_catalogue.service import ReportOrderingCatalogueService
from app.reporting_lineage.capture_service import PortfolioReviewInputCaptureError
from app.reporting_lineage.store import ReportInputSnapshotStore
from tests.unit.composite_reporting.fixtures import calculated_example
from tests.unit.composite_reporting.test_pooled_analysis import pair, selection
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    ControlledRenderBoundary,
    app,
    composite_lifecycle,
)
from tests.unit.composite_reporting.test_source_capture import job_for


def pooled_job(pin):
    return job_for(calculated_example()).model_copy(
        update={
            "tenant_id": pin.tenant_id,
            "portfolio_scope": {"composite_id": pin.composite_id},
            "as_of_date": pin.period_end,
            "reporting_currency": pin.reporting_currency,
            "options": {"composite_pooled_selection": pin.model_dump(mode="json")},
            "accepted_document_contract": {
                "input_snapshot_contract_version": "composite_review.v5"
            },
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "has space", "has\nline", "has\x7fcontrol"])
async def test_missing_or_malformed_deployment_credential_refuses_before_transport(
    monkeypatch, token
):
    async def forbidden(**kwargs):
        pytest.fail("Invalid credentials must never reach transport")

    monkeypatch.setattr("app.clients.performance_client.bounded_read_with_retry", forbidden)
    client = PerformanceClient("http://performance", 1, read_bearer_token=token)
    with pytest.raises(ValueError, match="READ_CREDENTIAL_REQUIRED"):
        await client.get_retained_composite_pooled_result(
            UUID(pair()[0]["calculation_id"]), admitted_tenant_id="controlled-tenant"
        )


@pytest.mark.asyncio
async def test_exact_get_uses_only_configured_bearer_and_admitted_tenant(monkeypatch):
    sent = []

    async def controlled(**kwargs):
        sent.append(kwargs)
        return 403, {"code": "PRINCIPAL_ADMISSION_DENIED"}

    monkeypatch.setattr("app.clients.performance_client.bounded_read_with_retry", controlled)
    monkeypatch.setattr(
        "app.clients.performance_client.propagation_headers",
        lambda: {
            "Authorization": "Bearer caller-not-forwarded",
            "X-Capabilities": "invented",
            "X-Tenant-Id": "foreign",
            "X-Correlation-Id": "test-correlation",
        },
    )
    client = PerformanceClient(
        "http://performance", 1, read_bearer_token="test-only-deployment-token"
    )
    identity = UUID(pair()[0]["calculation_id"])
    status, payload = await client.get_retained_composite_pooled_result(
        identity, admitted_tenant_id="controlled-tenant"
    )
    assert status == 403 and payload["code"] == "PRINCIPAL_ADMISSION_DENIED"
    assert (
        sent[0]["url"] == f"http://performance/performance/composites/analytics/results/{identity}"
    )
    assert sent[0]["headers"] == {
        "Authorization": "Bearer test-only-deployment-token",
        "X-Tenant-Id": "controlled-tenant",
        "X-Correlation-Id": "test-correlation",
    }
    assert sent[0]["json_body"] is None
    assert sent[0]["max_response_bytes"] == 8_388_608
    with pytest.raises(ValueError, match="EXACT_CALCULATION"):
        await client.get_retained_composite_pooled_result("../other", admitted_tenant_id="tenant")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [202, 401, 403, 404, 409])
async def test_refused_primary_retains_failure_lineage_without_reading_correction_parent(status):
    original, corrected = pair()
    pin = selection(corrected, original)
    reads = []

    class Source:
        async def get_retained_composite_pooled_result(self, calculation_id, **kwargs):
            reads.append(calculation_id)
            return status, {"status": "not-final", "code": "CONTROLLED_REFUSAL"}

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(performance_client=Source()).collect_for_job(pooled_job(pin))
    assert reads == [pin.calculation_id]
    call = caught.value.upstream_calls[0]
    assert call.status_code == status and call.method == "GET"
    assert call.response_payload == {"status": "not-final", "code": "CONTROLLED_REFUSAL"}
    assert call.completeness_status == call.supportability_status == "error"


@pytest.mark.asyncio
async def test_combined_primary_and_predecessor_capture_budget_refuses_without_truncation():
    original, corrected = pair()
    for result in (original, corrected):
        result["controlled_padding"] = "x" * 4_300_000
    pin = selection(corrected, original)
    results = {payload["calculation_id"]: payload for payload in (original, corrected)}
    reads = []

    class Source:
        async def get_retained_composite_pooled_result(self, calculation_id, **kwargs):
            reads.append(calculation_id)
            return 200, results[str(calculation_id)]

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(performance_client=Source()).collect_for_job(pooled_job(pin))
    assert "CAPTURE_CAPACITY_EXCEEDED" in str(caught.value.original_error)
    assert len(reads) == 2
    assert len(caught.value.upstream_calls[0].response_payload["controlled_padding"]) == 4_300_000


@pytest.mark.asyncio
@pytest.mark.parametrize("ready,overlong", [(False, False), (True, False), (True, True)])
async def test_v5_xlsx_requires_exact_capability_and_emits_persisted_package_only(
    ready, overlong, tmp_path, monkeypatch
):
    original = pair()[0]
    if overlong:
        original["controlled_note"] = "x" * 32_768
    pin = selection(original)
    capabilities, reads = [], []

    async def capability(self, **kwargs):
        capabilities.append(kwargs)
        return SimpleNamespace(state="ready" if ready else "unavailable")

    async def controlled(**kwargs):
        reads.append(kwargs["url"])
        return 200, deepcopy(original)

    monkeypatch.setattr(
        ReportOrderingCatalogueService, "document_contract_supportability", capability
    )
    monkeypatch.setattr("app.clients.performance_client.bounded_read_with_retry", controlled)
    monkeypatch.setattr(settings, "performance_read_bearer_token", SecretStr("unit-only"))
    boundary = ControlledRenderBoundary()
    with composite_lifecycle(tmp_path, monkeypatch, render_client=boundary) as (
        ledger,
        store,
        worker,
        _,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            ordered = await client.post(
                "/reports/composite-reviews",
                headers={**HEADERS, "X-Tenant-Id": pin.tenant_id},
                json={
                    "pooled_selection": pin.model_dump(mode="json"),
                    "requested_output_formats": ["xlsx"],
                },
            )
        assert capabilities == [
            {
                "report_type": "composite_review",
                "format_id": "xlsx",
                "contract_version": "composite_review.v5",
                "template_version": "v5",
            }
        ]
        if not ready:
            assert ordered.status_code == 503 and not reads and not boundary.packages
            return
        assert ordered.status_code == 202, ordered.text
        job_id = ordered.json()["report_job_id"]
        await worker.run_once(worker_id="pooled-unit-candidate", max_items=1, lease_seconds=30)
        if overlong:
            assert not boundary.packages
            assert (
                "COMPOSITE_POOLED_WORKBOOK_LITERAL_INVALID"
                in ledger.get_job(job_id).failure_message
            )
            record = store.get_snapshot_by_job(job_id)
            assert record.snapshot_payload["source_response"] == original
            assert ledger.get_job(job_id).archive_document_id is None and len(reads) == 1
            return
        assert len(boundary.packages) == 1, ledger.get_job(job_id).failure_message
        package = boundary.packages[0]
        record = store.get_snapshot_by_job(job_id)
        assert package["report_data"] == record.snapshot_payload
        assert package["report_data_contract_version"] == "composite_review.v5"
        assert package["template_version"] == "v5" and package["output_format"] == "xlsx"
        assert package["render_context"]["archive"]["composite_id"] == pin.composite_id
        assert (
            package["render_context"]["archive"]["report_revision_id"] == record.report_revision_id
        )
        projection = eligibility_workbook_projection(package)
        outcome = projection[1][1][0]
        assert "10.000000%" in outcome
        assert (
            record.snapshot_payload["source_response"]["outcome"]["return_value"]
            == (original["outcome"]["return_value"])
        )
        # The named receiver boundary declines rendering: no workbook/Archive claim.
        assert ledger.get_job(job_id).archive_document_id is None
        assert len(reads) == 1


@pytest.mark.asyncio
async def test_registered_original_correction_capture_and_reopen_preserve_source_without_refetch(
    tmp_path,
    monkeypatch,
):
    await exercise_registered_pooled_capture(tmp_path, monkeypatch)


async def exercise_registered_pooled_capture(tmp_path, monkeypatch, *, adapters=None):
    original, corrected = pair()
    responses = {payload["calculation_id"]: payload for payload in (original, corrected)}
    calls = []

    async def controlled(**kwargs):
        calls.append(kwargs["url"])
        assert kwargs["headers"]["Authorization"] == "Bearer controlled-unit-test-only"
        return 200, deepcopy(responses[kwargs["url"].rsplit("/", 1)[-1]])

    monkeypatch.setattr("app.clients.performance_client.bounded_read_with_retry", controlled)
    monkeypatch.setattr(
        settings, "performance_read_bearer_token", SecretStr("controlled-unit-test-only")
    )
    headers = {**HEADERS, "X-Tenant-Id": "controlled-tenant"}
    with composite_lifecycle(tmp_path, monkeypatch, adapters=adapters) as (
        ledger,
        store,
        worker,
        _,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            retained = []
            for payload, parent in ((original, None), (corrected, original)):
                pin = selection(payload, parent)
                request = {"pooled_selection": pin.model_dump(mode="json")}
                scoped = {
                    **headers,
                    "Idempotency-Key": HEADERS["Idempotency-Key"] + payload["calculation_id"],
                }
                ordered = await client.post(
                    "/reports/composite-reviews", headers=scoped, json=request
                )
                assert ordered.status_code == 202, ordered.text
                job_id = ordered.json()["report_job_id"]
                retry = await client.post(
                    "/reports/composite-reviews", headers=scoped, json=request
                )
                assert retry.json()["report_job_id"] == job_id
                completed = await worker.run_once(
                    worker_id="pooled-unit-worker", max_items=1, lease_seconds=30
                )
                assert completed.completed_count == 1
                job = ledger.get_job(job_id)
                assert job.status == "data_ready"
                assert (
                    job.accepted_document_contract["input_snapshot_contract_version"]
                    == "composite_review.v5"
                )
                snapshot = store.get_snapshot_by_job(job_id)
                assert snapshot.snapshot_payload["source_response"] == payload
                assert snapshot.snapshot_payload["predecessor_source_response"] == parent
                assert snapshot.report_revision_id
                assert len(store.list_upstream_calls(snapshot.snapshot_id)) == (
                    1 if parent is None else 2
                )
                assert len(snapshot.source_revision_vector["revisions"]) >= 3
                retained.append((job_id, snapshot.model_dump(mode="json")))
            assert len(calls) == 3
            responses.clear()
            for job_id, expected in retained:
                result = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=headers)
                assert result.status_code == 200 and result.json() == expected
                foreign = await client.get(
                    f"/reports/jobs/{job_id}/snapshot",
                    headers={**headers, "X-Tenant-Id": "foreign"},
                )
                assert foreign.status_code == 404
            reopened = (
                adapters()[1]
                if adapters is not None
                else ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
            )
            for job_id, expected in retained:
                assert reopened.get_snapshot_by_job(job_id).model_dump(mode="json") == expected
            assert len(calls) == 3
            return retained
