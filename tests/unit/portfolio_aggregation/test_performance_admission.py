"""Finite source values must also survive the existing public numeric boundary."""

from decimal import localcontext
from math import isfinite

import pytest

from app.services.aggregation_service import AggregationService


@pytest.mark.parametrize("value,expected", [("1E+309", None), ("-1E+309", None), ("1E+20", 1e20)])
def test_a_wider_decimal_context_cannot_publish_an_infinite_float(value, expected):
    payload = {
        "results_by_period": {
            "YTD": {"portfolio_twr": {"net": {"summary": {"cumulative_return": {"base": value}}}}}
        }
    }
    # Actual precision conversion, not a stubbed quantizer: a wider caller
    # context permits the Decimal but cannot widen the public float contract.
    with localcontext() as context:
        context.prec = 420
        row = AggregationService._admitted_ytd_return_row(payload)
    if expected is None:
        assert row is None
    else:
        assert row.value == expected and isfinite(row.value)
