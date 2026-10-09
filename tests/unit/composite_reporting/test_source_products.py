"""Accepted frozen75f synthetic source captures; no Report investment formulas."""

import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from app.composite_reporting.admission import admit_composite_response, response_digest
from app.composite_reporting.models import CompositeReportSelection, CompositeReviewJobRequest
from app.composite_reporting.product_contract import (
    CapturedReturnProduct,
    validate_composite_dataset,
)
from app.composite_reporting.product_tables import build_product_tables
from app.composite_reporting.source_capture import CompositeInputProvider
from app.composite_reporting.table_builder import build_composite_tables
from app.main import app
from app.reporting_identity.capture_binding import source_revision_vector_for_capture
from app.reporting_lineage.capture_service import PortfolioReviewInputCaptureError
from tests.unit.composite_reporting.fixtures import calculated_example
from tests.unit.composite_reporting.test_registered_lifecycle import HEADERS, composite_lifecycle
from tests.unit.composite_reporting.test_source_capture import job_for


@pytest.fixture(scope="module")
def source_packet():
    return load_source_packet()


def load_source_packet():
    raw = gzip.decompress(
        (Path(__file__).parent / "source_fixtures/source-products-75f.json.gz").read_bytes()
    )
    assert hashlib.sha256(raw).hexdigest() == (
        "86cceba7c9b3a9742184395e838d5b0c6136d6add345be2de04421686114323a"
    )
    return json.loads(raw)


def exact_selection(response):
    return CompositeReportSelection.model_validate(
        {
            "tenant_id": "tenant-a",
            **{
                key: response[key]
                for key in (
                    "calculation_id",
                    "composite_id",
                    "period_start",
                    "period_end",
                    "methodology",
                )
            },
            "reporting_currency": response["periods"][0]["reporting_currency"],
            "return_view": response["periods"][0]["return_view"],
            **{
                key: response["selection_manifest"][key]
                for key in (
                    "engine_version",
                    "calculation_fingerprint",
                    "windows",
                )
            },
            "response_digest": response_digest(response),
        }
    ).model_dump(mode="json")


def request_for(packet, version="original"):
    data = packet[version]
    return {
        "selection": exact_selection(data["primary"]),
        "source_products": [
            {
                "kind": "CALENDAR_RETURN",
                "product_key": "calendar_2020",
                "year": 2020,
                "selection": exact_selection(data["products"][0]["response"]),
            },
            {
                "kind": "TRAILING_RETURN",
                "product_key": "trailing_12m",
                "months": 12,
                "selection": exact_selection(data["products"][1]["response"]),
            },
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["original", "financial-correction"])
async def test_registered_capture_retains_all_three_actual_sources_and_retrieval(
    source_packet,
    version,
    tmp_path,
    monkeypatch,
    adapters=None,
):
    request = request_for(source_packet, version)
    sources = [
        source_packet[version]["primary"],
        *[
            item["response"]
            for item in source_packet[version]["products"]
            if item["http_status"] == 200
        ],
    ]
    by_id = {item["calculation_id"]: item for item in sources}
    sent = []

    async def source(**kwargs):
        sent.append(deepcopy(kwargs))
        return 200, deepcopy(by_id[kwargs["json_body"]["calculation_id"]])

    with composite_lifecycle(tmp_path, monkeypatch, adapters=adapters) as (
        ledger,
        store,
        worker,
        _,
    ):
        monkeypatch.setattr("app.clients.performance_client.post_with_retry", source)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            ordered = await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
            assert ordered.status_code == 202, ordered.text
            job_id = ordered.json()["report_job_id"]
            accepted = ledger.get_job(job_id)
            assert (
                accepted.accepted_document_contract["input_snapshot_contract_version"]
                == "composite_review.v2"
            )
            result = await worker.run_once(worker_id="products", max_items=1, lease_seconds=30)
            assert result.completed_count == 1
            assert ledger.get_job(job_id).status == "data_ready"
            stored = store.get_snapshot_by_job(job_id)
            from app.composite_reporting.render_package import composite_archive_custody

            custody = composite_archive_custody(job=accepted, record=stored)
            assert (
                custody["composite_report_identity"]["source_products"][0]["pin"]
                == request["source_products"][0]
            )
            changed_axes = {
                **accepted.accepted_document_contract,
                "report_data_contract_version": "composite_review.v1",
            }
            with pytest.raises(ValueError, match="COMPOSITE_CUSTODY_CONTRACT_CONFLICT"):
                composite_archive_custody(
                    job=accepted.model_copy(update={"accepted_document_contract": changed_axes}),
                    record=stored,
                )
            dataset = stored.snapshot_payload
            validate_composite_dataset(dataset)
            assert dataset["source_response"] == sources[0]
            assert [p["source_response"] for p in dataset["source_products"]] == sources[1:]
            assert len(sent) == 3
            assert all(call["headers"]["X-Tenant-Id"] == "tenant-a" for call in sent)
            assert [call["json_body"] for call in sent] == [
                CompositeReportSelection.model_validate(
                    exact_selection(source)
                ).performance_request()
                for source in sources
            ]
            assert len(store.list_upstream_calls(stored.snapshot_id)) == 3
            revisions = source_revision_vector_for_capture(
                snapshot_payload=dataset, upstream_services=("lotus-performance",)
            )
            assert {r.calculation_run_id for r in revisions.revisions} == set(by_id)
            tables = {table["table_id"]: table for table in dataset["tables"]}
            assert (
                tables["AnnualReturns"]["rows"][0]["cells"]["return"]["canonical_value"]
                == sources[1]["cumulative_return"]
            )
            assert (
                tables["TrailingReturns"]["rows"][0]["cells"]["return"]["canonical_value"]
                == sources[2]["cumulative_return"]
            )
            assert len(tables["MonthlyReturns"]["rows"]) == 72
            assert len(tables["Contribution"]["rows"]) == 2016
            assert dataset["publication_state"] == "NOT_ATTESTED"
            by_id.clear()
            replay = await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
            assert replay.json()["report_job_id"] == job_id
            retained = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=HEADERS)
            assert retained.status_code == 200
            assert store.get_snapshot_by_job(job_id).snapshot_hash == stored.snapshot_hash
            assert len(sent) == 3


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "wrong_months", "wrong_year", "foreign", "old_pins", "unknown_kind", "empty"],
)
def test_selector_conflicts_refuse_before_io(source_packet, mutation):
    request = request_for(source_packet)
    if mutation == "duplicate":
        request["source_products"].append(deepcopy(request["source_products"][0]))
    elif mutation == "wrong_months":
        request["source_products"][1]["months"] = 11
    elif mutation == "wrong_year":
        request["source_products"][0]["year"] = 2021
    elif mutation == "foreign":
        request["source_products"][0]["selection"]["tenant_id"] = "foreign"
    elif mutation == "old_pins":
        request["selection"] = request_for(source_packet, "financial-correction")["selection"]
    elif mutation == "unknown_kind":
        request["source_products"][0]["kind"] = "ANNUAL_DISPERSION"
    else:
        request["source_products"] = []
    with pytest.raises(ValueError):
        CompositeReviewJobRequest.model_validate(request)


def test_absent_products_preserve_legacy_request_options(source_packet):
    selection = request_for(source_packet)["selection"]
    request = CompositeReviewJobRequest.model_validate({"selection": selection})
    assert "source_products" not in request.model_dump(mode="json")
    assert request.capture_options() == {"composite_selection": selection}
    with pytest.raises(ValueError, match="typed source_products"):
        CompositeReviewJobRequest.model_validate(
            {"selection": selection, "options": {"composite_source_products": []}}
        )


def test_annual_refusal_has_no_metric_value(source_packet):
    for version in ("original", "financial-correction"):
        annual = source_packet[version]["products"][2]
        assert annual["http_status"] == 422
        assert "ANNUAL_DISPERSION_POLICY_BASIS_MISMATCH" in json.dumps(annual["response"])
        assert "value" not in annual["response"]


def test_shared_v2_schema_and_actual_trailing_example_are_executable():
    from app.composite_reporting.product_contract import composite_product_report_schema

    repo = next(
        parent for parent in Path(__file__).resolve().parents if (parent / "contracts").is_dir()
    )
    assert (
        json.loads((repo / "contracts/composite_review.v2.schema.json").read_text())
        == composite_product_report_schema()
    )
    example = json.loads((repo / "contracts/examples/composite-review.v2.json").read_text())
    validate_composite_dataset(example)
    assert len(example["source_response"]["periods"]) == 2
    assert example["source_products"][0]["source_response"]["cumulative_return"] == "0.030200000000"


@pytest.mark.parametrize("kind", ["calendar", "trailing"])
def test_single_kind_table_presence_and_no_empty_tables(source_packet, kind):
    request = CompositeReviewJobRequest.model_validate(request_for(source_packet))
    primary = build_composite_tables(
        admit_composite_response(
            selection=request.selection,
            admitted_tenant_id="tenant-a",
            status_code=200,
            payload=source_packet["original"]["primary"],
        )
    )
    index = 0 if kind == "calendar" else 1
    pin = request.source_products[index]
    dataset = build_product_tables(
        primary,
        [
            CapturedReturnProduct(
                pin=pin,
                endpoint="/composites/twr",
                method="POST",
                source_response_digest=pin.selection.response_digest,
                source_response=source_packet["original"]["products"][index]["response"],
            )
        ],
    )
    tables = {table["table_id"]: table for table in dataset["tables"]}
    assert len(tables) == (13 if kind == "calendar" else 14)
    assert all(table["rows"] for table in tables.values())
    if kind == "calendar":
        assert "TrailingReturns" not in tables
        assert len(tables["AnnualReturns"]["columns"]) == 11
    else:
        assert len(tables["AnnualReturns"]["columns"]) == 3
        assert tables["AnnualReturns"]["rows"][0]["cells"]["value"]["canonical_value"] is None
        assert (
            tables["TrailingReturns"]["rows"][0]["cells"]["return"]["source_pointer"]
            == "/source_products/0/source_response/cumulative_return"
        )


@pytest.fixture(scope="module")
def product_dataset(source_packet):
    request = CompositeReviewJobRequest.model_validate(request_for(source_packet))
    primary = build_composite_tables(
        admit_composite_response(
            selection=request.selection,
            admitted_tenant_id="tenant-a",
            status_code=200,
            payload=source_packet["original"]["primary"],
        )
    )
    products = [
        CapturedReturnProduct(
            pin=pin,
            endpoint="/composites/twr",
            method="POST",
            source_response_digest=pin.selection.response_digest,
            source_response=item["response"],
        )
        for pin, item in zip(
            request.source_products, source_packet["original"]["products"][:2], strict=True
        )
    ]
    return build_product_tables(primary, products)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_index",
        "leading_zero_index",
        "extra_financial_path",
        "period_financial_path",
        "wrong_value",
        "text_laundering",
        "percentage_points",
        "changed_digest",
        "changed_source",
        "forged_authority",
        "legacy_financial_in_facts",
        "unknown_endpoint",
        "oversize_table_id",
        "cross_kind",
        "wrong_label",
        "missing_table",
        "wrong_precision",
    ],
)
def test_product_financial_authority_and_schema_guards(product_dataset, mutation):
    data = deepcopy(product_dataset)
    table = next(t for t in data["tables"] if t["table_id"] == "AnnualReturns")
    cell = table["rows"][0]["cells"]["return"]
    column = next(c for c in table["columns"] if c["column_id"] == "return")
    if mutation == "wrong_index":
        cell["source_pointer"] = "/source_products/2/source_response/cumulative_return"
    elif mutation == "leading_zero_index":
        cell["source_pointer"] = "/source_products/00/source_response/cumulative_return"
    elif mutation == "extra_financial_path":
        cell["source_pointer"] = "/source_products/0/source_response/extra/cumulative_return"
    elif mutation == "period_financial_path":
        cell["source_pointer"] = "/source_products/0/source_response/periods/0/return_value"
    elif mutation == "wrong_value":
        cell["canonical_value"] = "0"
    elif mutation == "text_laundering":
        column.update(
            value_type="TEXT",
            unit="TEXT",
            display_unit="TEXT",
            display_conversion="IDENTITY",
            display_decimal_places=None,
        )
    elif mutation == "percentage_points":
        column["display_unit"] = "PERCENTAGE_POINTS"
    elif mutation == "changed_digest":
        data["source_products"][0]["source_response_digest"] = "sha256:" + "0" * 64
    elif mutation == "changed_source":
        data["source_products"][0]["source_response"]["cumulative_return"] = "0"
    elif mutation == "forged_authority":
        data["report_facts"]["authority"]["receipt"] = "official"
    elif mutation == "legacy_financial_in_facts":
        cell["source_pointer"] = "/report_facts/source_response_digest"
    elif mutation == "unknown_endpoint":
        data["source_products"][0]["endpoint"] = "/composites/latest"
    elif mutation == "cross_kind":
        trailing = next(t for t in data["tables"] if t["table_id"] == "TrailingReturns")
        table["rows"][0]["cells"] = deepcopy(trailing["rows"][0]["cells"])
    elif mutation == "wrong_label":
        column["label"] = "Annual member dispersion"
    elif mutation == "missing_table":
        data["tables"] = [t for t in data["tables"] if t["table_id"] != "TrailingReturns"]
    elif mutation == "wrong_precision":
        column["display_decimal_places"] = 6
    else:
        table["table_id"] = "x" * 32
    with pytest.raises(ValueError):
        validate_composite_dataset(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http503", "empty200", "changed200", "transport"])
async def test_required_product_failure_has_failed_lineage_and_no_partial_dataset(
    source_packet, failure
):
    request = CompositeReviewJobRequest.model_validate(request_for(source_packet))
    job = job_for(calculated_example()).model_copy(
        update={
            "options": request.capture_options(),
            "as_of_date": request.selection.period_end,
            "reporting_currency": request.selection.reporting_currency,
            "portfolio_scope": {"composite_id": request.selection.composite_id},
            "accepted_document_contract": {
                "input_snapshot_contract_version": "composite_review.v2"
            },
        }
    )

    class Source:
        async def get_composite_twr(self, payload, *, admitted_tenant_id):
            if payload["calculation_id"] == str(request.selection.calculation_id):
                return 200, source_packet["original"]["primary"]
            assert payload["calculation_id"] == str(
                request.source_products[0].selection.calculation_id
            )
            if failure == "transport":
                raise TimeoutError("controlled failure")
            if failure == "http503":
                return 503, {"detail": "unavailable"}
            if failure == "empty200":
                return 200, {}
            corrupt = deepcopy(source_packet["original"]["products"][0]["response"])
            corrupt["cumulative_return"] = "0"
            return 200, corrupt

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(performance_client=Source()).collect_for_job(job)
    calls = caught.value.upstream_calls
    assert len(calls) == 2
    assert calls[0].supportability_status == "complete"
    assert calls[-1].supportability_status == ("unavailable" if failure == "transport" else "error")


@pytest.mark.asyncio
async def test_v2_xlsx_unavailable_renderer_refuses_before_source_capture(
    source_packet,
    tmp_path,
    monkeypatch,
):
    from app.report_ordering_catalogue.models import ReportCatalogueSupportability
    from app.report_ordering_catalogue.router import get_report_ordering_catalogue_service

    class Unavailable:
        async def document_contract_supportability(self, **kwargs):
            assert kwargs["template_version"] == "v2"
            assert kwargs["contract_version"] == "composite_review.v2"
            return ReportCatalogueSupportability(
                state="unavailable",
                reason_code="template_version_not_registered",
                message="Unavailable",
            )

    with composite_lifecycle(tmp_path, monkeypatch) as (_, _, _, supplier):
        app.dependency_overrides[get_report_ordering_catalogue_service] = lambda: Unavailable()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews",
                json={**request_for(source_packet), "requested_output_formats": ["xlsx"]},
                headers=HEADERS,
            )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "composite_product_render_unavailable"
    assert supplier["calls"] == []
