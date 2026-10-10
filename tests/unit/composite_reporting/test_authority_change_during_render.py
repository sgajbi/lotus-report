"""Current supplier authority cannot replace the snapshot of an outstanding render.

Registered Report/SQLite and the shipped RenderClient are exercised here. Only
source and render HTTP transport are controlled; this unit proof claims no XLSX
or Archive completion. The held package can also feed an independent native reader.
"""

import asyncio
from copy import deepcopy

import httpx
import pytest

from app.clients.render_client import RenderClient
from app.composite_reporting.admission import CompositeEvidenceRefused, admit_composite_response
from app.main import app
from tests.unit.composite_reporting.fixtures import calculated_example, selection_for
from tests.unit.composite_reporting.test_registered_lifecycle import HEADERS, composite_lifecycle


class HeldRenderTransport:
    def __init__(self, consume_package=None):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.packages = []
        self.consume_package = consume_package

    async def post(self, *, json_body, headers, **kwargs):
        assert kwargs["url"] == "http://controlled-render/renders"
        assert headers["X-Tenant-Id"] == "tenant-a"
        self.packages.append(deepcopy(json_body))
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), timeout=10)
        # Inspect the actual transport argument after the change, not just the
        # copy captured on entry: shared mutable source references must fail.
        assert json_body == self.packages[0]
        if self.consume_package is not None:
            self.consume_package(deepcopy(json_body))
        return 503, {"failure_message": "Controlled receiver; no Archive completion claim."}


def change_current_authority(supplier, axis):
    changed = deepcopy(supplier["response"])
    changed["calculation_id"] = "00000000-0000-0000-0000-000000000100"
    changed["selection_manifest"]["calculation_fingerprint"] = "sha256:" + "e" * 64
    for window in changed["selection_manifest"]["windows"]:
        window[axis] = {
            "membership_content_hash": "sha256:" + "f" * 64,
            "method_binding": {"method_id": "ASSET_WEIGHTED", "method_version": "v2"},
            "source_cut_id": "changed-current-cut",
        }[axis]
    # Mutate the current object in place, rather than merely rebinding it, to
    # expose a snapshot/package retaining an alias to supplier-owned state.
    supplier["response"].clear()
    supplier["response"].update(changed)
    return changed


def large_money_example():
    """Scale the literal two-member fixture; preserve its supplied ratios and weights."""
    source = calculated_example()
    for period, end in zip(source["periods"], ("404", "408"), strict=True):
        period["beginning_market_value"] = "400" + "0" * 24
        period["ending_market_value"] = end + "0" * 24
        for member, amount in zip(period["member_contributions"], ("100", "300"), strict=True):
            member["beginning_market_value"] = amount + "0" * 24
    # Equal second-month member returns supply a literal measured zero; the
    # first-month dispersion remains unknown. No investment calculation here.
    source["periods"][1]["dispersion_equal_weight"] = "0E-12"
    return source


async def exercise_held_authority_change(
    tmp_path, monkeypatch, axis, *, consume_package=None, initial_source=None
):
    boundary = HeldRenderTransport(consume_package)
    monkeypatch.setattr("app.clients.render_client.post_with_retry", boundary.post)
    render = RenderClient(
        base_url="http://controlled-render",
        timeout_seconds=10,
        max_retries=0,
        retry_backoff_seconds=0,
    )
    with composite_lifecycle(tmp_path, monkeypatch, render_client=render) as stack:
        ledger, store, worker, supplier = stack
        if initial_source is not None:
            supplier["response"] = deepcopy(initial_source)
        original = deepcopy(supplier["response"])
        selection = selection_for(original).model_dump(mode="json")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            admitted = await client.post(
                "/reports/composite-reviews",
                json={"selection": selection, "requested_output_formats": ["xlsx"]},
                headers=HEADERS,
            )
            assert admitted.status_code == 202, admitted.text
            job_id = admitted.json()["report_job_id"]
            attempt = asyncio.create_task(
                worker.run_once(worker_id="held-render", max_items=1, lease_seconds=30)
            )
            try:
                await asyncio.wait_for(boundary.entered.wait(), timeout=10)
                assert not attempt.done()
                assert ledger.get_job(job_id).status == "rendering"
                retained = store.get_snapshot_by_job(job_id).model_dump(mode="json")
                assert retained["snapshot_payload"]["source_response"] == original
                changed = change_current_authority(supplier, axis)
                assert changed != original
                with pytest.raises(
                    CompositeEvidenceRefused, match="COMPOSITE_REPORT_RESPONSE_CHANGED"
                ):
                    admit_composite_response(
                        selection=selection_for(original),
                        admitted_tenant_id="tenant-a",
                        status_code=200,
                        payload=changed,
                    )
                read = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=HEADERS)
                assert read.status_code == 200
                assert read.json()["snapshot_hash"] == retained["snapshot_hash"]
                assert read.json()["snapshot_payload"]["source_response"] == original
            finally:
                boundary.release.set()
                await asyncio.wait_for(attempt, timeout=10)
            assert ledger.get_job(job_id).failure_category == "render_execution_failed"
            assert ledger.get_job(job_id).archive_document_id is None
            assert store.get_snapshot_by_job(job_id).model_dump(mode="json") == retained
            (package,) = boundary.packages
            assert package["report_data"] == retained["snapshot_payload"]
            assert package["report_data"]["selection"] == selection
            assert len(supplier["calls"]) == 1
            await require_new_authority_order(client, worker, ledger, store, selection, changed)
            assert len(supplier["calls"]) == 3
            assert store.get_snapshot_by_job(job_id).model_dump(mode="json") == retained
            return {
                "axis": axis,
                "captured_source": original,
                "changed_source": changed,
                "render_package": package,
                "snapshot": retained,
                "source_calls": len(supplier["calls"]),
            }


async def require_new_authority_order(client, worker, ledger, store, original_selection, changed):
    for label, selection in (
        ("stale-selection", original_selection),
        ("new-authority-selection", selection_for(changed).model_dump(mode="json")),
    ):
        response = await client.post(
            "/reports/composite-reviews",
            json={"selection": selection},
            headers={**HEADERS, "Idempotency-Key": label},
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["report_job_id"]
        await worker.run_once(worker_id=label, max_items=1, lease_seconds=30)
        record = store.get_snapshot_by_job(job_id)
        if label == "stale-selection":
            assert ledger.get_job(job_id).status == "failed"
            (call,) = store.list_upstream_calls(record.snapshot_id)
            assert call.status_code == 200
            assert call.failure_message == "Pinned composite source evidence failed admission."
            assert "source_response" not in record.snapshot_payload
            assert record.report_revision_id is None
        else:
            assert ledger.get_job(job_id).status == "data_ready"
            assert record.snapshot_payload["source_response"] == changed
            assert record.report_revision_id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("axis", ["membership_content_hash", "method_binding", "source_cut_id"])
@pytest.mark.parametrize("large_money", [False, True])
async def test_authority_change_while_rendering_preserves_capture_and_requires_new_order(
    tmp_path, monkeypatch, axis, large_money
):
    await exercise_held_authority_change(
        tmp_path,
        monkeypatch,
        axis,
        initial_source=large_money_example() if large_money else None,
    )
