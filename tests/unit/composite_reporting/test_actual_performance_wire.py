"""Consume an actual registered Performance wire, preserving its qualification.

The captured producer used PostgreSQL with controlled economic/authority inputs.
Report's transport here is a named replay, not a live domain-supplier claim.
"""

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from app.composite_reporting.admission import (
    CompositeEvidenceRefused,
    admit_composite_response,
    response_digest,
)
from app.composite_reporting.models import CompositeReportSelection
from app.main import app
from app.reporting_lineage.store import ReportInputSnapshotStore
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    ControlledRenderBoundary,
    composite_lifecycle,
)

FIXTURES = Path(__file__).with_name("wire_fixtures")


def actual_performance_selection(
    response_name="performance-main-c100-or02-response.json",
    provenance_name="performance-main-c100-provenance.json",
):
    response = json.loads((FIXTURES / response_name).read_text())
    provenance = json.loads((FIXTURES / provenance_name).read_text())
    assert provenance["producer_native_exit"] == 0
    selection = CompositeReportSelection.model_validate(
        {
            "tenant_id": provenance["admitted_tenant_from_authority_packets"],
            **{
                key: response[key]
                for key in (
                    "composite_id",
                    "calculation_id",
                    "period_start",
                    "period_end",
                    "methodology",
                )
            },
            "reporting_currency": provenance["request_reporting_currency"],
            "return_view": provenance["request_return_view"],
            **{
                key: response["selection_manifest"][key]
                for key in ("engine_version", "calculation_fingerprint", "windows")
            },
            "response_digest": response_digest(response),
        }
    )
    return response, selection


@pytest.mark.asyncio
@pytest.mark.parametrize("format_id", ["json", "xlsx"])
async def test_actual_financial_correction_retains_both_pinned_calculations(
    tmp_path, monkeypatch, format_id
):
    provenance_name = "performance-main-c100-pair-provenance.json"
    captures = {
        name: actual_performance_selection(
            f"performance-main-c100-pair-{name}.json", provenance_name
        )
        for name in ("original", "corrected")
    }
    original, corrected = (captures[name][0] for name in ("original", "corrected"))
    assert original["cumulative_return"] == "0.030200000000"
    assert corrected["cumulative_return"] == "0.055700000000"
    assert corrected["periods"][0]["return_value"] == "0.035000000000"
    # February's retained monthly evidence is unchanged; its cumulative
    # return includes the corrected January and therefore must differ.
    assert {
        key: value for key, value in original["periods"][1].items() if key != "cumulative_return"
    } == {
        key: value for key, value in corrected["periods"][1].items() if key != "cumulative_return"
    }
    boundary = ControlledRenderBoundary()
    with composite_lifecycle(tmp_path, monkeypatch, render_client=boundary) as stack:
        ledger, store, worker, supplier = stack
        retained = {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            for name, (response, selection) in captures.items():
                supplier["response"] = deepcopy(response)
                headers = {
                    **HEADERS,
                    "X-Tenant-Id": selection.tenant_id,
                    "Idempotency-Key": f"actual-pair-{name}",
                }
                request = {
                    "selection": selection.model_dump(mode="json"),
                    "requested_output_formats": [format_id],
                }
                ordered = await client.post(
                    "/reports/composite-reviews", json=request, headers=headers
                )
                assert ordered.status_code == 202, ordered.text
                job_id = ordered.json()["report_job_id"]
                run = await worker.run_once(
                    worker_id=f"actual-pair-{name}", max_items=1, lease_seconds=30
                )
                assert run.completed_count == 1
                job = ledger.get_job(job_id)
                if format_id == "json":
                    assert job.status == "data_ready"
                else:
                    assert job.status == "failed"
                    assert job.failure_category == "render_execution_failed"
                    assert boundary.packages[-1]["report_data"]["source_response"] == response
                snapshot = store.get_snapshot_by_job(job_id)
                assert snapshot.snapshot_payload["source_response"] == response
                assert snapshot.snapshot_payload["selection"] == selection.model_dump(mode="json")
                assert snapshot.snapshot_payload["publication_state"] == "NOT_ATTESTED"
                retained[name] = snapshot
                retry = await client.post(
                    "/reports/composite-reviews", json=request, headers=headers
                )
                assert retry.status_code == 202 and retry.json()["report_job_id"] == job_id
            assert len(supplier["calls"]) == 2
            assert (
                retained["original"].report_revision_id != retained["corrected"].report_revision_id
            )
            assert (
                retained["original"].source_revision_digest
                != retained["corrected"].source_revision_digest
            )
            reopened = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
            for name, snapshot in retained.items():
                read = await client.get(
                    f"/reports/jobs/{snapshot.report_job_id}/snapshot",
                    headers={**HEADERS, "X-Tenant-Id": captures[name][1].tenant_id},
                )
                assert read.status_code == 200
                assert read.json()["snapshot_payload"]["source_response"] == captures[name][0]
                assert reopened.get_snapshot_by_job(snapshot.report_job_id).model_dump(
                    mode="json"
                ) == (snapshot.model_dump(mode="json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("format_id", ["json", "xlsx"])
async def test_registered_actual_wire_retains_zero_precision_and_external_source_identity(
    tmp_path, monkeypatch, format_id
):
    response, selection = actual_performance_selection()
    boundary = ControlledRenderBoundary()
    with composite_lifecycle(tmp_path, monkeypatch, render_client=boundary) as stack:
        ledger, store, worker, supplier = stack
        supplier["response"] = deepcopy(response)
        headers = {**HEADERS, "X-Tenant-Id": selection.tenant_id}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            ordered = await client.post(
                "/reports/composite-reviews",
                json={
                    "selection": selection.model_dump(mode="json"),
                    "requested_output_formats": [format_id],
                },
                headers=headers,
            )
            assert ordered.status_code == 202, ordered.text
            job_id = ordered.json()["report_job_id"]
            run = await worker.run_once(worker_id="actual-wire", max_items=1, lease_seconds=30)
            if format_id == "json":
                assert run.completed_count == 1
                assert ledger.get_job(job_id).status == "data_ready"
                assert boundary.packages == []
            else:
                assert run.completed_count == 1
                assert ledger.get_job(job_id).status == "failed"
                assert ledger.get_job(job_id).failure_category == "render_execution_failed"
                assert len(boundary.packages) == 1
                assert boundary.packages[0]["report_data"]["source_response"] == response
                assert boundary.packages[0]["output_format"] == "xlsx"
            original = store.get_snapshot_by_job(job_id)
            assert original.snapshot_payload["source_response"] == response
            assert original.snapshot_payload["selection"] == selection.model_dump(mode="json")
            assert (
                original.snapshot_payload["source_response"]["periods"][1][
                    "dispersion_equal_weight"
                ]
                == "0E-12"
            )
            periods = next(
                table
                for table in original.snapshot_payload["tables"]
                if table["table_id"] == "MonthlyReturns"
            )
            zero = periods["rows"][1]["cells"]["dispersion_equal_weight"]
            assert zero["canonical_value"] == "0E-12"
            assert zero["availability"] == "AVAILABLE"
            assert zero["reason_codes"] == []
            assert original.snapshot_payload["publication_state"] == "NOT_ATTESTED"
            source = response["periods"][0]["member_contributions"][0]["source_authority_identity"]
            assert source["return_source_kind"] == "EXTERNAL_PROVIDER"
            assert original.source_revision_vector["revisions"][0]["content_hash"] == (
                selection.calculation_fingerprint
            )
            # Later transport content cannot alter retained producer values.
            supplier["response"]["cumulative_return"] = "999"
            retained = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=headers)
            assert retained.status_code == 200
            assert retained.json()["snapshot_payload"]["source_response"] == response
            assert len(supplier["calls"]) == 1
            assert supplier["calls"][0]["json_body"] == selection.performance_request()
            reopened = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
            assert reopened.get_snapshot_by_job(job_id).model_dump(mode="json") == (
                original.model_dump(mode="json")
            )


def test_actual_wire_refuses_changed_fee_identity_without_recalculating():
    response, selection = actual_performance_selection()
    wrong_fee = selection.model_copy(update={"return_view": "NET_ACTUAL"})
    with pytest.raises(CompositeEvidenceRefused, match="PERIOD_IDENTITY_MISMATCH"):
        admit_composite_response(
            selection=wrong_fee,
            admitted_tenant_id=selection.tenant_id,
            status_code=200,
            payload=response,
        )


def test_actual_wire_refuses_corruption_against_original_receipt_digest():
    response, selection = actual_performance_selection()
    response["periods"][1]["dispersion_equal_weight"] = "0.1"
    with pytest.raises(CompositeEvidenceRefused, match="RESPONSE_CHANGED"):
        admit_composite_response(
            selection=selection,
            admitted_tenant_id=selection.tenant_id,
            status_code=200,
            payload=response,
        )
