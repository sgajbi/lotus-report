"""Whole source populations and presentation semantics cannot silently disappear."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.composite_reporting.product_contract import validate_composite_dataset
from tests.unit.composite_reporting.test_table_contract import example_tables


@pytest.fixture(params=["v1", "v2"])
def dataset(request):
    if request.param == "v1":
        return example_tables()
    root = Path(__file__).resolve().parents[3]
    return json.loads((root / "contracts/examples/composite-review.v2.json").read_text())


def table(data, name):
    return next(item for item in data["tables"] if item["table_id"] == name)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_member_row",
        "missing_financial_column",
        "changed_rounding_precision",
        "changed_column_label",
        "changed_null_reason",
        "missing_period_row",
        "extra_member_row",
        "changed_row_identity",
        "changed_table_title",
        "coherent_disclosure_change",
    ],
)
def test_complete_projection_refuses_source_valid_but_misleading_tables(dataset, fault):
    validate_composite_dataset(dataset)
    corrupted = deepcopy(dataset)
    contribution = table(corrupted, "Contribution")
    if fault == "missing_member_row":
        contribution["rows"].pop()
    elif fault == "missing_financial_column":
        contribution["columns"] = [
            c for c in contribution["columns"] if c["column_id"] != "contribution"
        ]
        for row in contribution["rows"]:
            row["cells"].pop("contribution")
    elif fault in {"changed_rounding_precision", "changed_column_label"}:
        column = next(c for c in contribution["columns"] if c["column_id"] == "contribution")
        if fault == "changed_rounding_precision":
            assert column["display_decimal_places"] == 2
            column["display_decimal_places"] = 0
        else:
            assert column["label"] == "Return contribution (percentage points)"
            column["label"] = "Return (%)"
    elif fault == "changed_null_reason":
        cell = table(corrupted, "Risk")["rows"][0]["cells"]["value"]
        assert cell["canonical_value"] is None
        assert cell["reason_codes"] == ["SOURCE_PRODUCT_NOT_CAPTURED"]
        cell["reason_codes"] = ["APPROVED_ZERO"]
    elif fault == "missing_period_row":
        table(corrupted, "MonthlyReturns")["rows"].pop()
    elif fault == "extra_member_row":
        row = deepcopy(contribution["rows"][0])
        row["row_id"] = "extra"
        contribution["rows"].append(row)
    elif fault == "changed_row_identity":
        contribution["rows"][0]["row_id"] = "another-member"
    elif fault == "changed_table_title":
        contribution["title"] = "Approved contribution"
    else:
        corrupted["report_facts"]["disclosures"][0]["text"] = "Official approved results"
        table(corrupted, "Disclosures")["rows"][0]["cells"]["text"]["canonical_value"] = (
            "Official approved results"
        )
    with pytest.raises(ValueError, match="COMPOSITE_REPORT_PROJECTION_CONFLICT"):
        validate_composite_dataset(corrupted)
    assert dataset != corrupted


def test_valid_dataset_admission_preserves_exact_values_and_bytes(dataset):
    before = json.dumps(dataset, separators=(",", ":"), ensure_ascii=False)
    validated = validate_composite_dataset(dataset)
    assert validated.model_dump(mode="json") == dataset
    assert json.dumps(dataset, separators=(",", ":"), ensure_ascii=False) == before


def test_historical_complete_method_order_stays_valid_without_mutating_retention(dataset):
    methods = table(dataset, "Methods")
    pointers = dataset["report_facts"]["Methods"]
    dataset["report_facts"]["Methods"] = list(reversed(pointers))
    methods["rows"].reverse()
    for index, row in enumerate(methods["rows"]):
        row["row_id"] = str(index)
        row["cells"]["field"]["source_pointer"] = f"/report_facts/Methods/{index}"
    retained = deepcopy(dataset)
    assert validate_composite_dataset(dataset).model_dump(mode="json") == retained
    assert dataset == retained


@pytest.mark.parametrize("fault", ["missing_method", "duplicate_method", "false_ordinal"])
def test_historical_method_compatibility_requires_complete_exact_source_paths(dataset, fault):
    methods = table(dataset, "Methods")
    if fault == "missing_method":
        methods["rows"].pop()
        dataset["report_facts"]["Methods"].pop()
    elif fault == "duplicate_method":
        dataset["report_facts"]["Methods"][-1] = dataset["report_facts"]["Methods"][0]
        row = deepcopy(methods["rows"][0])
        row["row_id"] = str(len(methods["rows"]) - 1)
        row["cells"]["field"]["source_pointer"] = (
            f"/report_facts/Methods/{len(methods['rows']) - 1}"
        )
        methods["rows"][-1] = row
    else:
        dataset["report_facts"]["Methods"].reverse()
        methods["rows"].reverse()
        # Coherent cell pointers remain valid, but historical row IDs are forged.
        for index, row in enumerate(methods["rows"]):
            row["row_id"] = "forged-" + str(index)
            row["cells"]["field"]["source_pointer"] = f"/report_facts/Methods/{index}"
    with pytest.raises(ValueError, match="COMPOSITE_REPORT_PROJECTION_CONFLICT"):
        validate_composite_dataset(dataset)
