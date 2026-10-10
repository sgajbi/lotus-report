"""Native PostgreSQL publication cursors are ordered, not necessarily adjacent."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.eligibility_admission import source_hash
from app.composite_reporting.historical_admission import require_retained_receipt
from app.composite_reporting.historical_tables import build_historical_dataset
from app.composite_reporting.models import HistoricalEligibilitySelection

FIXTURES = (
    Path(__file__).parents[2] / "fixtures/composite-historical-policy/native-publication-sequences"
)
NATIVE = {
    "cases": {
        key: value
        for path in sorted(FIXTURES.glob("*.json"))
        for key, value in json.loads(path.read_bytes())["cases"].items()
    }
}
assert len(NATIVE["cases"]) == 3


@pytest.mark.parametrize("phase", NATIVE["cases"])
def test_native_correction_shapes_accept_nonadjacent_publication_sequences(phase):
    case = NATIVE["cases"][phase]
    selection = HistoricalEligibilitySelection.model_validate(
        case["request"]["eligibility_selection"]
    )
    months = deepcopy(case["source_months"])
    data = build_historical_dataset(selection, months)
    assert data["source_months"] == case["source_months"]
    assert len(data["tables"]) == 10
    receipts = months[0]["lineage_receipts"] + (
        [months[0]["receipt"]] if "receipt" in months[0] else []
    )
    assert any(
        receipt["product_version"] == "v4"
        and receipt["publication_sequence"]
        > receipt["lineage"]["expected_current_publication_sequence"] + 1
        for receipt in receipts
    )


def correction_receipt():
    case = NATIVE["cases"]["v2-correction-2-published"]
    selection = HistoricalEligibilitySelection.model_validate(
        case["request"]["eligibility_selection"]
    )
    return selection, deepcopy(case["source_months"][0]["receipt"])


@pytest.mark.parametrize("offset", [1, 2, 100])
def test_retained_receipt_accepts_strictly_later_sequence(offset):
    """Unsigned rehashed clones isolate ordering; they establish no producer authority."""
    selection, receipt = correction_receipt()
    receipt["publication_sequence"] = (
        receipt["lineage"]["expected_current_publication_sequence"] + offset
    )
    receipt["content_hash"] = source_hash(
        {key: value for key, value in receipt.items() if key != "content_hash"}
    )
    require_retained_receipt(selection, receipt)


@pytest.mark.parametrize("offset", [0, -1])
def test_retained_receipt_refuses_equal_or_backward_sequence(offset):
    selection, receipt = correction_receipt()
    receipt["publication_sequence"] = (
        receipt["lineage"]["expected_current_publication_sequence"] + offset
    )
    receipt["content_hash"] = source_hash(
        {key: value for key, value in receipt.items() if key != "content_hash"}
    )
    with pytest.raises(
        CompositeEvidenceRefused, match="COMPOSITE_HISTORICAL_SOURCE_BINDING_CONFLICT"
    ):
        require_retained_receipt(selection, receipt)


@pytest.mark.parametrize("binding", ["parent_publication", "lineage_receipt", "tenant"])
def test_nonadjacent_sequences_preserve_exact_custody_refusals(binding):
    case = deepcopy(NATIVE["cases"]["v2-correction-2-published"])
    selection = case["request"]["eligibility_selection"]
    month = case["source_months"][0]
    if binding == "parent_publication":
        month["parent_publication"]["membership_content_hash"] = "sha256:" + "0" * 64
    elif binding == "lineage_receipt":
        selection["months"][0]["lineage_receipts"][0]["receipt_content_hash"] = "sha256:" + "0" * 64
    else:
        selection["tenant_id"] = "foreign-tenant"
    with pytest.raises(ValueError):
        build_historical_dataset(HistoricalEligibilitySelection.model_validate(selection), [month])
