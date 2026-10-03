from copy import deepcopy
from itertools import product

import pytest
from pydantic import ValidationError

from app.models.contracts import AllocationSupportability
from app.portfolio_aggregation.allocation import build_allocation_rows


@pytest.mark.parametrize(
    "status,reason,weights,currency",
    list(
        product(
            ("available", "empty", "unavailable"),
            AllocationSupportability.model_json_schema()["properties"]["reason_code"]["enum"],
            ("source", "derived", "mixed", None),
            ("USD", None),
        )
    ),
)
def test_typed_allocation_posture_refuses_contradictory_states(status, reason, weights, currency):
    valid = (
        (
            status == "available"
            and reason == "allocation_complete"
            and weights is not None
            and currency == "USD"
        )
        or (status == "empty" and reason == "allocation_empty" and weights is None)
        or (
            status == "unavailable"
            and reason not in {"allocation_complete", "allocation_empty"}
            and weights is None
        )
    )
    data = {
        "status": status,
        "reason_code": reason,
        "weight_source": weights,
        "reporting_currency": currency,
    }
    if valid:
        assert AllocationSupportability.model_validate(data).status == status
    else:
        with pytest.raises(ValidationError):
            AllocationSupportability.model_validate(data)


def _allocation():
    return {
        "reporting_currency": "USD",
        "total_market_value_reporting_currency": "100",
        "views": [
            {
                "dimension": "asset_class",
                "buckets": [
                    {
                        "dimension_value": "Equity",
                        "market_value_reporting_currency": "120",
                        "weight": "1.2",
                    },
                    {"dimension_value": "Cash", "market_value_reporting_currency": "-20"},
                ],
            }
        ],
    }


def test_mixed_weight_provenance_and_finite_signed_source_are_preserved():
    source = _allocation()
    original = deepcopy(source)
    rows, posture = build_allocation_rows(source, "100", summary_currency="USD")
    assert [(row.bucket, row.value) for row in rows] == [("CASH", -20), ("EQUITY", 120)]
    assert posture.status == "available" and posture.weight_source == "mixed"
    assert source == original


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing_bucket", "source_bucket_total_mismatch"),
        ("duplicate_bucket", "incomplete_allocation_payload"),
        ("duplicate_view", "incomplete_allocation_payload"),
        ("null_coverage", "source_valuation_unavailable"),
        ("tiny_denominator", "incomplete_allocation_payload"),
    ],
)
def test_incoherent_scope_is_explicitly_withheld(mutation, reason):
    source = _allocation()
    buckets = source["views"][0]["buckets"]
    if mutation == "missing_bucket":
        buckets.pop()
    elif mutation == "duplicate_bucket":
        buckets[1]["dimension_value"] = "equity"
    elif mutation == "duplicate_view":
        source["views"].append(deepcopy(source["views"][0]))
    elif mutation == "null_coverage":
        source["valuation_coverage"] = None
    else:
        source["total_market_value_reporting_currency"] = "1e-1000"
        return_value = "1e-1000"
        rows, posture = build_allocation_rows(source, return_value, summary_currency="USD")
        assert rows == [] and posture.reason_code == reason
        return
    rows, posture = build_allocation_rows(source, 100, summary_currency="USD")
    assert rows == [] and posture.reason_code == reason
