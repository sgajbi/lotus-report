"""Representative bad inputs must fail; uncertainty must remain typed evidence."""

from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from app.composite_reporting.admission import CompositeEvidenceRefused, admit_composite_response
from app.composite_reporting.models import CompositeReportSelection
from app.composite_reporting.semantic_contract import CompositeReportData
from app.composite_reporting.source_capture import CompositeInputProvider
from app.composite_reporting.table_contract import (
    CompositeCell,
    CompositeTable,
    resolve_source_pointer,
    validate_cell_lineage,
)
from app.main import app
from app.reporting_document_format import document_output_format
from app.reporting_identity.capture_binding import source_revision_vector_for_capture
from app.reporting_render.package_builder import _build_render_package
from tests.unit.composite_reporting.fixtures import calculated_example, selection_for
from tests.unit.composite_reporting.test_registered_lifecycle import HEADERS, composite_lifecycle
from tests.unit.composite_reporting.test_source_capture import job_for
from tests.unit.composite_reporting.test_table_contract import example_tables


@pytest.mark.parametrize(
    "formats,expected",
    [(None, None), (["json"], None), (["json", "pdf"], "pdf"), (["xlsx"], "xlsx")],
)
def test_one_document_format_is_explicit(formats, expected):
    assert document_output_format(formats) == expected


def test_two_document_formats_cannot_share_one_lifecycle():
    with pytest.raises(ValueError, match="MULTIPLE_DOCUMENT"):
        document_output_format(["pdf", "xlsx"])


@pytest.mark.parametrize("json_only", [False, True])
def test_composite_render_requires_a_document_order_and_persisted_record(json_only):
    job = job_for(calculated_example())
    if json_only:
        job = job.model_copy(update={"requested_output_formats": ["json"]})
    with pytest.raises(ValueError, match="COMPOSITE_RENDER_"):
        _build_render_package(
            job=job, snapshot=example_tables(), render_job_id="candidate", snapshot_id="unpersisted"
        )


@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        {"selection_manifest": None},
        {"selection_manifest": {}},
        {"selection_manifest": {"calculation_fingerprint": "sha256:" + "a" * 64}},
    ],
)
def test_missing_source_stated_revision_is_unknown_not_a_report_hash(source):
    payload = {
        "contract_version": "composite_review.v1",
        "source_response": source,
        "source_response_digest": "sha256:" + "b" * 64,
    }
    vector = source_revision_vector_for_capture(
        snapshot_payload=payload, upstream_services=("lotus-performance",)
    )
    assert vector.coverage == "unknown"
    assert len(vector.revisions) == 1
    assert vector.revisions[0].source_service == "lotus-performance"
    assert vector.revisions[0].content_hash is None


@pytest.mark.parametrize("defect", ["reversed", "blank-method", "wrong-horizon"])
def test_invalid_window_or_horizon_is_refused(defect):
    selection = selection_for(calculated_example()).model_dump(mode="json")
    if defect == "reversed":
        selection["windows"][0]["period_end"] = "2025-12-31"
    elif defect == "blank-method":
        selection["windows"][0]["method_binding"] = {"method_id": " "}
    else:
        selection["period_end"] = "2026-03-31"
    with pytest.raises(ValidationError):
        CompositeReportSelection.model_validate(selection)


@pytest.mark.parametrize("status", ["BLOCKED", "READY"])
def test_summary_readiness_cannot_disagree_with_retained_periods(status):
    payload = calculated_example()
    payload["status"] = status
    payload["periods"][0]["status"] = "DEGRADED"
    with pytest.raises(CompositeEvidenceRefused):
        admit_composite_response(
            selection=selection_for(payload),
            admitted_tenant_id="tenant-a",
            status_code=200,
            payload=payload,
        )


@pytest.mark.parametrize("defect", ["digest", "authority", "publication"])
def test_semantic_handoff_cannot_promote_or_rebind_source(defect):
    dataset = example_tables()
    if defect == "digest":
        dataset["source_response_digest"] = "sha256:" + "f" * 64
    elif defect == "authority":
        dataset["report_facts"]["authority"]["receipt"] = "invented-approval"
    else:
        dataset["report_facts"]["publication_state"] = "OFFICIAL"
    with pytest.raises(ValueError, match="COMPOSITE_REPORT_"):
        CompositeReportData.model_validate(dataset)


@pytest.mark.asyncio
async def test_degraded_source_retains_partial_lineage_and_unavailable_member_table():
    payload = calculated_example()
    payload["status"] = "DEGRADED"
    payload["cumulative_return"] = None
    for period in payload["periods"]:
        period.update(
            status="BLOCKED",
            return_value=None,
            cumulative_return=None,
            member_count=0,
            member_contributions=[],
            reason_codes=["MEMBERS_UNAVAILABLE"],
        )

    class Source:
        async def get_composite_twr(self, *args, **kwargs):
            return 200, deepcopy(payload)

    capture = await CompositeInputProvider(performance_client=Source()).collect_for_job(
        job_for(payload)
    )
    assert capture.upstream_calls[0].supportability_status == "partial"
    assert capture.upstream_calls[0].completeness_status == "partial"
    contribution = next(
        table for table in capture.snapshot_payload["tables"] if table["table_id"] == "Contribution"
    )
    assert len(contribution["rows"]) == 1
    assert all(
        cell["canonical_value"] is None for cell in contribution["rows"][0]["cells"].values()
    )


@pytest.mark.parametrize(
    "pointer",
    ["/source_response/periods/01", "/source_response/status/x", "/source_response/periods/99"],
)
def test_noncanonical_or_unresolved_source_pointers_are_refused(pointer):
    with pytest.raises(CompositeEvidenceRefused, match="POINTER_INVALID"):
        resolve_source_pointer(example_tables(), pointer)


@pytest.mark.parametrize(
    "defect",
    [
        "null-ready",
        "reason",
        "columns",
        "rows",
        "tables",
        "financial-pointer",
        "boolean-source",
        "currency",
        "nonfinite",
        "negative-count",
    ],
)
def test_cells_cannot_hide_ambiguous_values_or_population(defect):
    dataset = example_tables()
    monthly = next(table for table in dataset["tables"] if table["table_id"] == "MonthlyReturns")
    cell = monthly["rows"][0]["cells"]["return_value"]
    if defect in {"null-ready", "reason"}:
        cell["canonical_value"] = None if defect == "null-ready" else cell["canonical_value"]
        cell["reason_codes"] = ["unsafe reason"] if defect == "reason" else []
        with pytest.raises(ValidationError):
            CompositeCell.model_validate(cell)
        return
    if defect in {"columns", "rows"}:
        key = defect
        monthly[key].append(deepcopy(monthly[key][0]))
        with pytest.raises(ValidationError):
            CompositeTable.model_validate(monthly)
        return
    if defect == "tables":
        dataset["tables"].append(deepcopy(monthly))
    elif defect == "financial-pointer":
        cell["source_pointer"] = "/report_facts/qualification"
    elif defect == "boolean-source":
        summary = dataset["tables"][0]
        summary["rows"][0]["cells"]["composite_id"]["source_pointer"] = "/source_response/injected"
        dataset["source_response"]["injected"] = True
    elif defect == "currency":
        next(column for column in monthly["columns"] if column["value_type"] == "MONEY")[
            "currency"
        ] = "SGD"
    elif defect == "nonfinite":
        dataset["source_response"]["periods"][0]["return_value"] = "Infinity"
        cell["canonical_value"] = "Infinity"
    else:
        dataset["source_response"]["periods"][0]["member_count"] = -1
        monthly["rows"][0]["cells"]["member_count"]["canonical_value"] = "-1"
    with pytest.raises(CompositeEvidenceRefused):
        validate_cell_lineage(
            dataset, [CompositeTable.model_validate(t) for t in dataset["tables"]]
        )


@pytest.mark.asyncio
async def test_missing_retry_identity_and_conflicting_replay_are_refused(tmp_path, monkeypatch):
    with composite_lifecycle(tmp_path, monkeypatch) as stack:
        _, _, _, supplier = stack
        request = {"selection": selection_for(supplier["response"]).model_dump(mode="json")}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            no_key = {key: value for key, value in HEADERS.items() if key != "Idempotency-Key"}
            assert (
                await client.post("/reports/composite-reviews", json=request, headers=no_key)
            ).status_code == 400
            assert (
                await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
            ).status_code == 202
            request["selection"]["calculation_fingerprint"] = "sha256:" + "f" * 64
            assert (
                await client.post("/reports/composite-reviews", json=request, headers=HEADERS)
            ).status_code == 409
        assert supplier["calls"] == []
