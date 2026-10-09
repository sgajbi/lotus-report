"""A new explicitly corrected order cannot overwrite the original retained facts."""

from copy import deepcopy

import httpx
import pytest

from app.main import app
from tests.unit.composite_reporting.fixtures import selection_for
from tests.unit.composite_reporting.test_registered_lifecycle import HEADERS, composite_lifecycle


@pytest.mark.asyncio
async def test_original_and_explicit_corrected_orders_keep_distinct_immutable_revisions(
    tmp_path, monkeypatch
):
    with composite_lifecycle(tmp_path, monkeypatch) as stack:
        ledger, store, worker, supplier = stack
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            original_request = {
                "selection": selection_for(supplier["response"]).model_dump(mode="json")
            }
            original_order = await client.post(
                "/reports/composite-reviews", json=original_request, headers=HEADERS
            )
            assert original_order.status_code == 202
            original_id = original_order.json()["report_job_id"]
            await worker.run_once(worker_id="original", max_items=1, lease_seconds=30)
            original = store.get_snapshot_by_job(original_id)
            original_copy = original.model_dump(mode="json")

            corrected = deepcopy(supplier["response"])
            corrected["calculation_id"] = "00000000-0000-0000-0000-000000000100"
            corrected["selection_manifest"]["calculation_fingerprint"] = "sha256:" + "e" * 64
            window = corrected["selection_manifest"]["windows"][-1]
            window.update(
                materialization_id="00000000-0000-0000-0000-000000000003",
                restatement_sequence=2,
                source_cut_id="corrected-cut-2",
                retained_receipt_fingerprint="sha256:" + "f" * 64,
            )
            period = corrected["periods"][-1]
            period.update(
                return_value="0.030000000000",
                cumulative_return="0.040300000000",
                restatement_sequence=2,
                restatement_versions=["corrected-v2"],
                ending_market_value="412",
            )
            for member, contribution in zip(
                period["member_contributions"], ("0.0075", "0.0225"), strict=True
            ):
                member.update(
                    return_value="0.03",
                    contribution=contribution,
                    restatement_sequence=2,
                    restatement_version="corrected-v2",
                )
            corrected["cumulative_return"] = "0.040300000000"
            supplier["response"] = corrected
            corrected_request = {"selection": selection_for(corrected).model_dump(mode="json")}
            corrected_order = await client.post(
                "/reports/composite-reviews",
                json=corrected_request,
                headers={**HEADERS, "Idempotency-Key": "explicit-corrected-order"},
            )
            assert corrected_order.status_code == 202
            corrected_id = corrected_order.json()["report_job_id"]
            await worker.run_once(worker_id="corrected", max_items=1, lease_seconds=30)
            revised = store.get_snapshot_by_job(corrected_id)
            assert ledger.get_job(corrected_id).status == "data_ready"
            assert revised.report_revision_id != original.report_revision_id
            assert revised.source_revision_digest != original.source_revision_digest
            assert revised.snapshot_payload["source_response"] == corrected
            assert store.get_snapshot_by_job(original_id).model_dump(mode="json") == original_copy
            old_read = await client.get(f"/reports/jobs/{original_id}/snapshot", headers=HEADERS)
            new_read = await client.get(f"/reports/jobs/{corrected_id}/snapshot", headers=HEADERS)
            assert (
                old_read.json()["snapshot_payload"]["source_response"]["cumulative_return"]
                == "0.030200000000"
            )
            assert (
                new_read.json()["snapshot_payload"]["source_response"]["cumulative_return"]
                == "0.040300000000"
            )
            assert len(supplier["calls"]) == 2
