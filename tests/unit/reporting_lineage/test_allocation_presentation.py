"""The document presents the dimensions the caller ordered (issue #224).

Before this contract existed, Report discarded the caller's
`allocation_dimensions` and shipped all seven breakdowns flat, so Render chose by
its own first-with-rows priority. Measured jointly with the Render session
against both mains on 2026-09-01:

    ordered currency     -> By currency    correct, by luck of ordering
    ordered region       -> By currency    WRONG
    ordered sector       -> By currency    WRONG
    ordered country      -> By currency    WRONG
    ordered product_type -> By currency    WRONG
    ordered rating       -> By currency    WRONG

Six of seven single-dimension orders presented a dimension the advisor never
asked for - and because the appendix defines whichever view is drawn, the wrong
document was internally consistent about it. Those six are pinned here.
"""

from __future__ import annotations

import pytest

from app.reporting_lineage.allocation_presentation import (
    ALLOCATION_DIMENSIONS,
    resolve_allocation_presentation,
)

_SOURCE_KEYS = dict(ALLOCATION_DIMENSIONS)


def _snapshot_with_every_dimension() -> dict[str, object]:
    return {
        "allocation": {
            source_key: [{"name": f"{dimension}-bucket", "weight_pct": "100"}]
            for dimension, source_key in ALLOCATION_DIMENSIONS
        }
    }


@pytest.mark.parametrize("dimension", [name for name, _ in ALLOCATION_DIMENSIONS])
def test_a_single_dimension_order_presents_that_dimension(dimension: str) -> None:
    """The regression table above: each of the seven, not just the one that
    happened to lead a downstream priority list."""

    presentation = resolve_allocation_presentation(
        options={"allocation_dimensions": [dimension]},
        snapshot=_snapshot_with_every_dimension(),
    )

    assert presentation["resolved_by"] == "caller_request"
    assert [entry["dimension"] for entry in presentation["dimensions"]] == [dimension]
    assert presentation["dimensions"][0]["package_key"] == f"by_{dimension}"
    assert presentation["dimensions"][0]["posture"] == "ready"


def test_a_multi_dimension_order_is_presented_in_the_order_asked_for() -> None:
    presentation = resolve_allocation_presentation(
        options={"allocation_dimensions": ["rating", "sector", "currency"]},
        snapshot=_snapshot_with_every_dimension(),
    )

    assert [entry["dimension"] for entry in presentation["dimensions"]] == [
        "rating",
        "sector",
        "currency",
    ]


def test_omitting_the_option_resolves_to_the_default_policy() -> None:
    """`asset_class_when_omitted` is a statement about silence: an order that
    chooses nothing still presents asset class, so the fix can only change the
    callers who explicitly asked for something else."""

    presentation = resolve_allocation_presentation(
        options={},
        snapshot=_snapshot_with_every_dimension(),
    )

    assert presentation["resolved_by"] == "report_default_policy"
    assert [entry["dimension"] for entry in presentation["dimensions"]] == ["asset_class"]


def test_asset_class_is_one_dimension_among_seven_and_not_privileged() -> None:
    """A caller who asked for sector has not asked for an asset-class
    composition. The catalogue publishes the seven as equal multi-select
    options; no compliance requirement for a mandatory asset-allocation view is
    encoded anywhere, and if one is ever added it belongs in the catalogue."""

    presentation = resolve_allocation_presentation(
        options={"allocation_dimensions": ["sector"]},
        snapshot=_snapshot_with_every_dimension(),
    )

    assert [entry["dimension"] for entry in presentation["dimensions"]] == ["sector"]
    assert "asset_class" not in {entry["dimension"] for entry in presentation["dimensions"]}


def test_empty_and_unavailable_are_different_answers() -> None:
    """`empty` is a fact about the PORTFOLIO (the source answered, there are no
    buckets) and is drawn; `unavailable` is a fact about the DATA (the source
    did not answer) and is said. A presence check cannot tell them apart, which
    is why posture is authoritative rather than inferred from row counts."""

    snapshot = {"allocation": {"bySector": []}}

    presentation = resolve_allocation_presentation(
        options={"allocation_dimensions": ["sector", "rating"]},
        snapshot=snapshot,
    )

    postures = {entry["dimension"]: entry["posture"] for entry in presentation["dimensions"]}
    assert postures == {"sector": "empty", "rating": "unavailable"}


def test_an_unavailable_dimension_is_never_silently_substituted() -> None:
    """The defect this contract removes: answering an unanswerable request with
    a different dimension that happens to have rows."""

    snapshot = {"allocation": {"byCurrency": [{"name": "USD", "weight_pct": "100"}]}}

    presentation = resolve_allocation_presentation(
        options={"allocation_dimensions": ["sector"]},
        snapshot=snapshot,
    )

    assert [entry["dimension"] for entry in presentation["dimensions"]] == ["sector"]
    assert presentation["dimensions"][0]["posture"] == "unavailable"


def test_duplicate_and_unsupported_values_do_not_reach_the_package() -> None:
    presentation = resolve_allocation_presentation(
        options={"allocation_dimensions": ["sector", "sector", "issuer", 7, " currency "]},
        snapshot=_snapshot_with_every_dimension(),
    )

    assert [entry["dimension"] for entry in presentation["dimensions"]] == ["sector", "currency"]


def test_a_malformed_allocation_payload_is_unavailable_not_ready() -> None:
    presentation = resolve_allocation_presentation(
        options={"allocation_dimensions": ["sector"]},
        snapshot={"allocation": {"bySector": "not-a-list"}},
    )

    assert presentation["dimensions"][0]["posture"] == "unavailable"
