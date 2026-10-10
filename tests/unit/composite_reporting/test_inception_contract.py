"""Definition custody, coverage and complete SI projection refusal controls."""

import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from app.composite_reporting.admission import admit_composite_response, response_digest
from app.composite_reporting.eligibility_admission import source_hash
from app.composite_reporting.models import (
    CompositeReviewJobRequest,
    CompositeSourceContextSelection,
)
from app.composite_reporting.source_context_contract import (
    CompositeSourceContextReportData,
    project_source_context,
    source_context_report_schema,
)
from tests.unit.composite_reporting.fixtures import selection_for
from tests.unit.composite_reporting.inception_examples import inception_example


def dataset_for(selection, source, captured):
    primary = admit_composite_response(
        selection=selection, admitted_tenant_id=selection.tenant_id, status_code=200, payload=source
    )
    return project_source_context(
        primary,
        CompositeSourceContextSelection(definition=captured.pin, since_inception=True),
        captured,
        [],
    )


@pytest.mark.parametrize(
    "name", ["si-positive", "si-later-window", "history-original", "history-corrected-si"]
)
def test_published_controlled_v8_examples_match_executable_contract(name):
    from tests.unit.composite_reporting.test_supplied_history import history_dataset

    contracts = Path(__file__).resolve().parents[3] / "contracts" / "examples"
    expected = (
        history_dataset(name == "history-corrected-si", name == "history-corrected-si")
        if name.startswith("history")
        else dataset_for(
            *inception_example("2025-12-31" if name == "si-later-window" else "2026-01-01")
        )
    )
    assert (
        json.loads(
            (contracts / f"composite-review.v8.{name}.expected.json").read_text(encoding="utf-8")
        )
        == expected
    )
    request = CompositeReviewJobRequest.model_validate(
        json.loads(
            (contracts / f"composite-review.v8.{name}.request.json").read_text(encoding="utf-8")
        )
    )
    assert request.selection.model_dump(mode="json") == expected["selection"]
    assert request.source_context.model_dump(mode="json") == expected["source_context"]
    jsonschema.validate(expected, source_context_report_schema())


@pytest.mark.parametrize("inception,available", [("2026-01-01", True), ("2025-12-31", False)])
def test_inception_requires_source_start_and_preserves_window_value(inception, available):
    selection, source, captured = inception_example(inception)
    data = dataset_for(selection, source, captured)
    CompositeSourceContextReportData.model_validate(data)
    jsonschema.validate(data, source_context_report_schema())
    row = next(t for t in data["tables"] if t["table_id"] == "SinceInception")["rows"][0]
    cell = row["cells"]["return"]
    assert cell["canonical_value"] == ("0.030200000000" if available else None)
    assert cell["availability"] == ("AVAILABLE" if available else "UNAVAILABLE")
    assert cell["reason_codes"] == (
        [] if available else ["SOURCE_VECTOR_BEGINS_AFTER_PINNED_INCEPTION"]
    )
    assert data["source_response"] == source
    assert data["definition"]["source_response"] == captured.source_response


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "foreign"),
        ("composite_id", "other"),
        ("reporting_currency", "EUR"),
        ("definition_version", "d2"),
        ("product_version", "v2"),
        ("inception_date", "2026-01-02"),
        ("termination_date", "2026-01-31"),
    ],
)
def test_wrong_or_changed_definition_refuses_even_when_response_is_repinned(field, value):
    selection, source, captured = inception_example()
    raw = deepcopy(captured.source_response)
    raw[field] = value
    raw["content_hash"] = source_hash(raw, recursive=True)
    changed = captured.model_copy(
        update={
            "source_response": raw,
            "source_response_digest": response_digest(raw),
            "pin": captured.pin.model_copy(update={"response_digest": response_digest(raw)}),
        }
    )
    with pytest.raises(ValueError):
        dataset_for(selection, source, changed)


def test_definition_horizon_before_inception_or_after_termination_refuses():
    for field, value in [("inception_date", "2026-01-02"), ("termination_date", "2026-01-31")]:
        selection, source, captured = inception_example()
        raw = deepcopy(captured.source_response)
        raw[field] = value
        raw["content_hash"] = source_hash(raw, recursive=True)
        for window in source["selection_manifest"]["windows"]:
            window["definition_content_hash"] = raw["content_hash"]
        selection = selection_for(source)
        digest = response_digest(raw)
        captured = captured.model_copy(
            update={
                "source_response": raw,
                "source_response_digest": digest,
                "pin": captured.pin.model_copy(
                    update={"content_hash": raw["content_hash"], "response_digest": digest}
                ),
            }
        )
        with pytest.raises(ValueError, match="COMPOSITE_INCEPTION_HORIZON_CONFLICT"):
            dataset_for(selection, source, captured)


def test_hash_and_raw_digest_are_distinct_required_guards():
    selection, source, captured = inception_example()
    raw = deepcopy(captured.source_response)
    raw["display_name"] = "altered"
    digest = response_digest(raw)
    invalid = captured.model_copy(
        update={
            "source_response": raw,
            "source_response_digest": digest,
            "pin": captured.pin.model_copy(update={"response_digest": digest}),
        }
    )
    with pytest.raises(ValueError, match="SOURCE_HASH_CONFLICT"):
        dataset_for(selection, source, invalid)
    raw["content_hash"] = source_hash(raw, recursive=True)
    invalid = captured.model_copy(update={"source_response": raw})
    with pytest.raises(ValueError, match="DEFINITION_CONFLICT"):
        dataset_for(selection, source, invalid)


@pytest.mark.parametrize(
    "mutation", ["value", "pointer", "units", "reason", "drop_row", "drop_table"]
)
def test_complete_projection_rejects_semantic_corruption(mutation):
    selection, source, captured = inception_example()
    data = dataset_for(selection, source, captured)
    table = next(t for t in data["tables"] if t["table_id"] == "SinceInception")
    if mutation == "drop_table":
        data["tables"].remove(table)
    elif mutation == "drop_row":
        data["tables"][0]["rows"].pop()
    elif mutation == "units":
        column = next(c for c in table["columns"] if c["column_id"] == "return")
        column.update(display_unit="PERCENTAGE_POINTS")
    else:
        cell = table["rows"][0]["cells"]["return"]
        if mutation == "value":
            cell["canonical_value"] = "0.99"
        elif mutation == "pointer":
            cell["source_pointer"] = "/source_response/periods/0/return_value"
        else:
            cell.update(canonical_value=None, availability="UNAVAILABLE", reason_codes=["FAKE"])
    with pytest.raises(ValueError):
        CompositeSourceContextReportData.model_validate(data)


def test_nonready_vector_cannot_earn_si_and_gaps_cannot_be_admitted():
    selection, source, captured = inception_example()
    source["status"] = "DEGRADED"
    source["periods"][0]["status"] = "DEGRADED"
    selection = selection_for(source)
    data = dataset_for(selection, source, captured)
    assert data["report_facts"]["since_inception"]["value"] is None
    assert (
        data["report_facts"]["since_inception"]["reason"] == "COMPLETE_READY_SOURCE_VECTOR_REQUIRED"
    )
    CompositeSourceContextReportData.model_validate(data)
    source["selection_manifest"]["windows"][1]["period_start"] = "2026-02-02"
    with pytest.raises(ValueError, match="chronological and contiguous"):
        selection_for(source)


def test_request_forbids_unbound_and_hidden_definition_options():
    selection, _, captured = inception_example()
    request = {
        "selection": selection.model_dump(mode="json"),
        "source_context": {"definition": captured.pin.model_dump(), "since_inception": True},
    }
    assert (
        CompositeReviewJobRequest.model_validate(request).source_context.definition == captured.pin
    )
    request["source_context"]["definition"]["inception_date"] = "2026-01-01"
    with pytest.raises(ValueError):
        CompositeReviewJobRequest.model_validate(request)
    request.pop("source_context")
    request["options"] = {
        "composite_source_context": {
            "definition": captured.pin.model_dump(),
            "since_inception": True,
        }
    }
    with pytest.raises(ValueError, match="typed source_context"):
        CompositeReviewJobRequest.model_validate(request)


def test_published_schema_and_examples_match_executable_contract():
    import json

    root = Path(__file__).parents[3]
    assert (
        json.loads((root / "contracts/composite_review.v8.schema.json").read_bytes())
        == source_context_report_schema()
    )


def test_aggregate_raw_capture_limit_refuses_instead_of_truncating():
    selection, source, captured = inception_example()
    source["controlled_oversize_evidence"] = "x" * 8_388_608
    selection = selection_for(source)
    with pytest.raises(ValueError, match="CAPTURE_CAPACITY_EXCEEDED"):
        dataset_for(selection, source, captured)
