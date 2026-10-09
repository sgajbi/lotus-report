"""Source-bound linked consumption, complete projection and retained identity controls."""

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

from app.composite_reporting.admission import response_digest
from app.composite_reporting.linked_contract import (
    CompositeLinkedReportData,
    composite_linked_report_schema,
)
from app.composite_reporting.linked_tables import build_linked_dataset
from app.composite_reporting.models import (
    CompositeReviewJobRequest,
    LinkedAnalysisSelection,
    SourceNumber,
)
from app.composite_reporting.render_package import composite_archive_custody
from app.main import app
from tests.unit.composite_reporting.test_registered_lifecycle import HEADERS, composite_lifecycle


def load_linked_packet():
    return json.loads(
        (Path(__file__).parent / "source_fixtures/linked-contribution-6e9.json").read_text()
    )


def linked_request(packet, version="original"):
    return {"linked_selection": packet["pair"][version]["selection"]}


def linked_dataset(version="original"):
    pair = load_linked_packet()["pair"][version]
    selection = LinkedAnalysisSelection.model_validate(pair["selection"])
    return build_linked_dataset(
        selection=selection,
        admitted_tenant_id=selection.tenant_id,
        status_code=200,
        payload=deepcopy(pair["response"]),
    )


def omit_complete_column(data):
    table = data["tables"][1]
    column = table["columns"].pop()
    for row in table["rows"]:
        row["cells"].pop(column["column_id"])


def append_complete_column(data):
    table = data["tables"][1]
    column = deepcopy(table["columns"][0])
    original_id = column["column_id"]
    column["column_id"] = "extra_source_column"
    table["columns"].append(column)
    for row in table["rows"]:
        row["cells"][column["column_id"]] = deepcopy(row["cells"][original_id])


@pytest.mark.parametrize("mutation", [omit_complete_column, append_complete_column])
def test_coherent_source_bound_column_changes_are_refused(mutation):
    data = linked_dataset()
    mutation(data)
    with pytest.raises(ValueError):
        CompositeLinkedReportData.model_validate(data)


@pytest.mark.parametrize("case", ["none", "both", "products", "numeric_sequence", "options"])
def test_primary_operation_and_pinned_vector_are_exclusive(case):
    from tests.unit.composite_reporting.test_source_products import load_source_packet, request_for

    legacy = request_for(load_source_packet())
    request = linked_request(load_linked_packet())
    if case == "none":
        request = {}
    elif case == "both":
        request["selection"] = legacy["selection"]
    elif case == "products":
        request["source_products"] = legacy["source_products"]
    elif case == "numeric_sequence":
        request["linked_selection"]["source_request"]["restatement_sequence"] = 1
    else:
        request["options"] = {"composite_linked_selection": request["linked_selection"]}
    with pytest.raises(ValueError):
        CompositeReviewJobRequest.model_validate(request)


def test_legacy_request_serialization_and_capture_options_preserve_identity():
    from tests.unit.composite_reporting.test_source_products import load_source_packet, request_for

    for products in (False, True):
        request = request_for(load_source_packet())
        if not products:
            request.pop("source_products")
        validated = CompositeReviewJobRequest.model_validate(request)
        assert validated.model_dump(mode="json") == {
            **request,
            "requested_output_formats": ["json"],
            "options": {},
        }
        expected = {"composite_selection": request["selection"]}
        if products:
            expected["composite_source_products"] = request["source_products"]
        assert validated.capture_options() == expected
        assert response_digest(validated.capture_options()) == response_digest(expected)


@pytest.mark.parametrize("version", ["original", "corrected"])
def test_genuine_current_source_complete_projection_and_schema(version):
    packet = load_linked_packet()
    data = linked_dataset(version)
    source = packet["pair"][version]["response"]
    assert data["source_response"] == source
    assert len(source["members"]) == 2 and len(source["periods"]) == 4
    assert all(row["source_authority_identity"] for row in source["periods"])
    assert data["selection"]["source_request"] == packet["pair"][version]["request"]
    Draft202012Validator(composite_linked_report_schema()).validate(data)
    assert len(data["tables"]) == 7
    assert source["cumulative_return"] == ("0.030200" if version == "original" else "0.04040")
    assert data["publication_state"] == "NOT_ATTESTED"
    # Literal source totals, not a Report linking/summing calculation.
    assert (
        data["tables"][0]["rows"][0]["cells"]["total_linked_contribution"]["canonical_value"]
        == source["total_linked_contribution"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["tables"].pop(),
        lambda d: d["tables"].append(deepcopy(d["tables"][0])),
        lambda d: d["tables"][0].update(table_id="Extra"),
        lambda d: d["tables"][1]["rows"].pop(),
        lambda d: d["tables"][2]["rows"].pop(),
        lambda d: d["tables"][1]["rows"].append(deepcopy(d["tables"][1]["rows"][0])),
        lambda d: d["tables"][2]["rows"].append(
            {**deepcopy(d["tables"][2]["rows"][0]), "row_id": "extra"}
        ),
        lambda d: d["tables"][1]["columns"].pop(),
        lambda d: d["tables"][1]["columns"].append(deepcopy(d["tables"][1]["columns"][0])),
        lambda d: d["tables"][1]["columns"][1].update(display_unit="PERCENT"),
        lambda d: d["tables"][2]["columns"][7].update(value_type="TEXT"),
        lambda d: d["tables"][2]["columns"][4].update(currency="EUR"),
        lambda d: d["tables"][1]["rows"][0]["cells"]["linked_contribution"].update(
            canonical_value="0"
        ),
        lambda d: d["tables"][1]["rows"][0]["cells"]["linked_contribution"].update(
            source_pointer="/source_response/members/00/linked_contribution"
        ),
        lambda d: d["report_facts"]["uncaptured"].pop(),
        lambda d: d.update(tables=[]),
        lambda d: d.update(publication_state="OFFICIAL"),
        lambda d: d["selection"].update(tenant_id="foreign"),
    ],
)
def test_surviving_valid_pointers_cannot_hide_incomplete_or_mutated_layout(mutation):
    data = linked_dataset()
    mutation(data)
    with pytest.raises(ValueError):
        CompositeLinkedReportData.model_validate(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["members"].pop(),
        lambda r: r["periods"].pop(),
        lambda r: r["members"].append(deepcopy(r["members"][0])),
        lambda r: r["periods"].append(deepcopy(r["periods"][0])),
        lambda r: r["members"][0].update(participating_period_count=1),
        lambda r: r["periods"][0].pop("source_authority_identity"),
        lambda r: r["periods"][0].update(restatement_sequence=999),
        lambda r: r.update(return_view="NET_ACTUAL"),
        lambda r: r.update(reporting_currency="EUR"),
        lambda r: r.update(method="OTHER"),
        lambda r: r.update(cumulative_return="NaN"),
    ],
)
def test_source_population_scope_and_finiteness_refuse_even_with_rebound_digest(mutation):
    pair = deepcopy(load_linked_packet()["pair"]["original"])
    mutation(pair["response"])
    pair["selection"]["response_digest"] = response_digest(pair["response"])
    with pytest.raises(ValueError):
        build_linked_dataset(
            selection=LinkedAnalysisSelection.model_validate(pair["selection"]),
            admitted_tenant_id="synthetic-tenant-a",
            status_code=200,
            payload=pair["response"],
        )


@pytest.mark.parametrize("value", ["0E-12", "-0e+12", "1.25E-7", "0", "-0.0001", 0])
def test_finite_scientific_decimal_current_schema_and_native_admit_without_normalization(value):
    adapter = TypeAdapter(SourceNumber)
    adapter.validate_python(value)
    Draft202012Validator(adapter.json_schema()).validate(value)


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-Infinity", "1e", "--1", "", True, None, 1.25, {}, []]
)
def test_scientific_schema_does_not_admit_nonfinite_or_invalid_json_types(value):
    adapter = TypeAdapter(SourceNumber)
    assert not Draft202012Validator(adapter.json_schema()).is_valid(value)
    with pytest.raises(ValueError):
        adapter.validate_python(value)


def test_generated_schema_and_executable_source_bound_example():
    repo = next(p for p in Path(__file__).resolve().parents if (p / "contracts").is_dir())
    schema = json.loads((repo / "contracts/composite_review.v3.schema.json").read_text())
    assert schema == composite_linked_report_schema()
    data = json.loads((repo / "contracts/examples/composite-review.v3.json").read_text())
    assert data == linked_dataset()
    Draft202012Validator(schema).validate(data)
    CompositeLinkedReportData.model_validate(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["original", "corrected"])
async def test_registered_linked_capture_and_retained_custody(
    tmp_path,
    monkeypatch,
    version,
    adapters=None,
):
    packet = load_linked_packet()
    request = linked_request(packet, version)
    headers = {
        **HEADERS,
        "X-Tenant-Id": "synthetic-tenant-a",
        "Idempotency-Key": HEADERS["Idempotency-Key"] + "-linked-" + version,
    }
    with composite_lifecycle(tmp_path, monkeypatch, adapters=adapters) as stack:
        ledger, store, worker, supplier = stack
        supplier["response"] = packet["pair"][version]["response"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            ordered = await client.post("/reports/composite-reviews", json=request, headers=headers)
            assert ordered.status_code == 202, ordered.text
            job_id = ordered.json()["report_job_id"]
            accepted = ledger.get_job(job_id)
            assert (
                accepted.accepted_document_contract["input_snapshot_contract_version"]
                == "composite_review.v3"
            )
            assert (
                await worker.run_once(worker_id="linked", max_items=1, lease_seconds=30)
            ).completed_count == 1
            assert ledger.get_job(job_id).status == "data_ready"
            snapshot = store.get_snapshot_by_job(job_id)
            assert snapshot.snapshot_payload == linked_dataset(version)
            assert supplier["calls"][0]["url"].endswith("/composites/analytics")
            assert supplier["calls"][0]["headers"]["X-Tenant-Id"] == "synthetic-tenant-a"
            assert supplier["calls"][0]["json_body"] == packet["pair"][version]["request"]
            custody = composite_archive_custody(job=accepted, record=snapshot)
            assert custody["composite_report_identity"]["selection"] == request["linked_selection"]
            assert custody["composite_report_identity"]["contract_version"] == "composite_review.v3"
            assert (
                snapshot.source_revision_vector["revisions"][0]["content_hash"]
                == request["linked_selection"]["calculation_fingerprint"]
            )
            supplier["response"] = {"unavailable": "changed after capture"}
            retrieved = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=headers)
            assert retrieved.status_code == 200
            assert retrieved.json()["snapshot_hash"] == snapshot.snapshot_hash
            assert len(supplier["calls"]) == 1
            assert (
                await client.get(
                    f"/reports/jobs/{job_id}/snapshot",
                    headers={**headers, "X-Tenant-Id": "foreign"},
                )
            ).status_code == 404
    return job_id


@pytest.mark.asyncio
async def test_v3_xlsx_refuses_without_exact_renderer_and_does_not_capture(tmp_path, monkeypatch):
    from app.report_ordering_catalogue.router import get_report_ordering_catalogue_service

    class Unavailable:
        async def document_contract_supportability(self, **kwargs):
            assert kwargs["contract_version"] == "composite_review.v3"
            assert kwargs["template_version"] == "v3"
            from types import SimpleNamespace

            return SimpleNamespace(state="unavailable")

    with composite_lifecycle(tmp_path, monkeypatch) as stack:
        app.dependency_overrides[get_report_ordering_catalogue_service] = lambda: Unavailable()
        request = {**linked_request(load_linked_packet()), "requested_output_formats": ["xlsx"]}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews",
                json=request,
                headers={**HEADERS, "X-Tenant-Id": "synthetic-tenant-a"},
            )
            assert response.status_code == 503
            assert stack[3]["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http503", "empty200", "changed200", "transport"])
async def test_failed_linked_source_has_failed_lineage_and_no_partial_dataset(failure):
    from app.composite_reporting.source_capture import CompositeInputProvider
    from app.reporting_lineage.capture_service import PortfolioReviewInputCaptureError
    from tests.unit.composite_reporting.fixtures import calculated_example
    from tests.unit.composite_reporting.test_source_capture import job_for

    packet = load_linked_packet()
    request = CompositeReviewJobRequest.model_validate(linked_request(packet))
    selection = request.primary_selection
    job = job_for(calculated_example()).model_copy(
        update={
            "tenant_id": selection.tenant_id,
            "options": request.capture_options(),
            "as_of_date": selection.period_end,
            "reporting_currency": selection.reporting_currency,
            "portfolio_scope": {"composite_id": selection.composite_id},
            "accepted_document_contract": {
                "input_snapshot_contract_version": "composite_review.v3"
            },
        }
    )

    class Source:
        async def get_composite_analytics(self, payload, *, admitted_tenant_id):
            assert payload == packet["pair"]["original"]["request"]
            assert admitted_tenant_id == selection.tenant_id
            if failure == "transport":
                raise TimeoutError("controlled linked source failure")
            if failure == "http503":
                return 503, {"detail": "unavailable"}
            if failure == "empty200":
                return 200, {}
            changed = deepcopy(packet["pair"]["original"]["response"])
            changed["cumulative_return"] = "0"
            return 200, changed

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(performance_client=Source()).collect_for_job(job)
    calls = caught.value.upstream_calls
    assert len(calls) == 1
    assert calls[0].endpoint == "/composites/analytics"
    assert calls[0].supportability_status == ("unavailable" if failure == "transport" else "error")


@pytest.mark.asyncio
async def test_original_and_explicit_correction_retain_distinct_source_identity(
    tmp_path, monkeypatch
):
    packet = load_linked_packet()
    with composite_lifecycle(tmp_path, monkeypatch) as (ledger, store, worker, supplier):
        retained = {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            for version in ("original", "corrected"):
                supplier["response"] = packet["pair"][version]["response"]
                headers = {
                    **HEADERS,
                    "X-Tenant-Id": "synthetic-tenant-a",
                    "Idempotency-Key": "linked-pair-" + version,
                }
                response = await client.post(
                    "/reports/composite-reviews",
                    json=linked_request(packet, version),
                    headers=headers,
                )
                assert response.status_code == 202, response.text
                job_id = response.json()["report_job_id"]
                await worker.run_once(worker_id="linked-pair", max_items=1, lease_seconds=30)
                assert ledger.get_job(job_id).status == "data_ready"
                retained[version] = store.get_snapshot_by_job(job_id)
            original, corrected = retained["original"], retained["corrected"]
            assert original.report_revision_id != corrected.report_revision_id
            assert original.source_revision_digest != corrected.source_revision_digest
            assert original.factual_content_digest != corrected.factual_content_digest
            supplier["response"] = {"detail": "no further source permitted"}
            for version, snapshot in retained.items():
                response = await client.get(
                    f"/reports/jobs/{snapshot.report_job_id}/snapshot", headers=headers
                )
                assert response.status_code == 200
                assert store.get_snapshot_by_job(snapshot.report_job_id) == snapshot
                assert snapshot.snapshot_payload == linked_dataset(version)
            assert len(supplier["calls"]) == 2
