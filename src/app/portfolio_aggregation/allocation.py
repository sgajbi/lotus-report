"""A complete signed net allocation, or an explicit reason it cannot be published.

Core owns valuations and any weights it supplies. Missing legacy weights can be
derived from those valuations; a stated null or malformed weight cannot.
"""

from decimal import Decimal
from typing import Any

from app.models.contracts import AggregationRow, AllocationSupportability
from app.precision_policy import MONEY_SCALE, PERFORMANCE_SCALE, quantize_performance, to_decimal


class _Unavailable(ValueError):
    pass


def _number(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        raise _Unavailable("incomplete_allocation_payload")
    try:
        number = to_decimal(value)
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise _Unavailable("incomplete_allocation_payload") from exc
    if not number.is_finite():
        raise _Unavailable("incomplete_allocation_payload")
    return number


def _buckets(allocation: dict[str, Any]) -> list[Any]:
    coverage = allocation.get("valuation_coverage")
    if "valuation_coverage" in allocation:
        if not isinstance(coverage, dict) or coverage.get("coverage_state") not in {
            "COMPLETE",
            "MEASURED_ZERO",
            "CARRY_FORWARD",
            "LOADED_EMPTY",
        }:
            raise _Unavailable("source_valuation_unavailable")
    views = allocation.get("views")
    if not isinstance(views, list):
        raise _Unavailable("incomplete_allocation_payload")
    selected = [
        view for view in views if isinstance(view, dict) and view.get("dimension") == "asset_class"
    ]
    if len(selected) != 1:
        raise _Unavailable("incomplete_allocation_payload")
    buckets = selected[0].get("buckets")
    if not isinstance(buckets, list):
        raise _Unavailable("incomplete_allocation_payload")
    return buckets


def _denominator(allocation: dict[str, Any], summary_total: object) -> Decimal:
    denominator = _number(summary_total)
    if denominator <= 0:
        raise _Unavailable("nonpositive_net_denominator")
    if "total_market_value_reporting_currency" in allocation:
        source_total = _number(allocation["total_market_value_reporting_currency"])
        if source_total != denominator:
            raise _Unavailable("source_denominator_mismatch")
    return denominator


def _row(bucket: object, denominator: Decimal) -> tuple[AggregationRow, Decimal, bool]:
    if not isinstance(bucket, dict):
        raise _Unavailable("incomplete_allocation_payload")
    label = bucket.get("dimension_value")
    if not isinstance(label, str) or not label.strip():
        raise _Unavailable("incomplete_allocation_payload")
    amount = _number(bucket.get("market_value_reporting_currency"))
    derived = "weight" not in bucket
    expected = quantize_performance(amount / denominator * 100)
    actual = expected if derived else quantize_performance(_number(bucket["weight"]) * 100)
    if abs(actual - expected) > PERFORMANCE_SCALE:
        raise _Unavailable("source_weight_mismatch")
    return (
        AggregationRow.model_validate(
            {"bucket": label.strip().upper(), "metric": "weight_pct", "value": actual}
        ),
        amount,
        derived,
    )


def build_allocation_rows(
    allocation: object, summary_total: object, *, summary_currency: object = None
) -> tuple[list[AggregationRow], AllocationSupportability]:
    """Preserve signed and zero components; never renormalize a filtered subset."""
    if not isinstance(allocation, dict):
        return [], AllocationSupportability(
            status="unavailable", reason_code="incomplete_allocation_payload"
        )
    currency = allocation.get("reporting_currency")
    currency = currency if isinstance(currency, str) and currency.strip() else None
    try:
        if not currency or not isinstance(summary_currency, str) or not summary_currency.strip():
            raise _Unavailable("reporting_currency_unavailable")
        if currency != summary_currency:
            raise _Unavailable("reporting_currency_mismatch")
        denominator = _denominator(allocation, summary_total)
        buckets = _buckets(allocation)
        if not buckets:
            return [], AllocationSupportability(
                status="empty", reason_code="allocation_empty", reporting_currency=currency
            )
        items = [_row(bucket, denominator) for bucket in buckets]
        if abs(sum((item[1] for item in items), Decimal("0")) - denominator) > MONEY_SCALE:
            raise _Unavailable("source_bucket_total_mismatch")
        rows = sorted((item[0] for item in items), key=lambda row: row.bucket)
        if len({row.bucket for row in rows}) != len(rows):
            raise _Unavailable("incomplete_allocation_payload")
        derived_count = sum(item[2] for item in items)
        weight_source = (
            "derived" if derived_count == len(items) else "source" if not derived_count else "mixed"
        )
        return rows, AllocationSupportability.model_validate(
            {
                "status": "available",
                "reason_code": "allocation_complete",
                "weight_source": weight_source,
                "reporting_currency": currency,
            }
        )
    except (ValueError, TypeError, ArithmeticError) as exc:
        reason = str(exc) if isinstance(exc, _Unavailable) else "incomplete_allocation_payload"
        return [], AllocationSupportability.model_validate(
            {"status": "unavailable", "reason_code": reason, "reporting_currency": currency}
        )
