"""Which allocation dimensions a document presents, and in what order (issue #224).

Report owns this decision; Render draws what it is told. Before this existed the
render package shipped all seven `by_*` breakdowns flat with the caller's
`allocation_dimensions` discarded, so Render selected by its own first-with-rows
priority - and six of seven single-dimension orders presented a dimension the
advisor never asked for, in a document whose appendix then agreed with the wrong
choice.

The selection is resolved once at composition time and persisted into the
immutable snapshot, so "which dimensions did this document present?" is answered
by the snapshot rather than by replaying today's defaulting policy against an old
order.

`asset_class` is one dimension among seven and carries no privileged status: the
catalogue publishes them as equal multi-select options with
`asset_class_when_omitted` defaulting, which is a statement about silence, not a
mandate. No compliance requirement for a mandatory asset-allocation view exists
in the catalogue; if one is ever encoded it belongs there (the field becomes
required, or an always-present view is declared), never as an implicit privilege
in a renderer.
"""

from __future__ import annotations

from typing import Any

#: Governed dimensions in Report's default presentation order, paired with the
#: upstream snapshot key each is read from.
ALLOCATION_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("asset_class", "byAssetClass"),
    ("currency", "byCurrency"),
    ("sector", "bySector"),
    ("region", "byRegion"),
    ("country", "byCountry"),
    ("product_type", "byProductType"),
    ("rating", "byRating"),
)
DEFAULT_ALLOCATION_DIMENSIONS: tuple[str, ...] = ("asset_class",)

POSTURE_READY = "ready"
POSTURE_EMPTY = "empty"
POSTURE_UNAVAILABLE = "unavailable"


def resolve_allocation_presentation(
    *,
    options: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """The resolved, ordered dimension set with each dimension's posture.

    The list includes defaults, so no consumer reconstructs Report policy to
    know what to draw, and `posture` is authoritative:

    - ``ready``       the source answered with buckets - draw it;
    - ``empty``       the source answered with none - a fact about the
                      PORTFOLIO, drawn as an empty statement;
    - ``unavailable`` the source did not answer at all - a fact about the DATA,
                      said rather than drawn.

    Presence of rows cannot express that difference, and inferring it back from
    row counts is the inference this contract removes.
    """

    requested = requested_allocation_dimensions(options)
    selection = requested or list(DEFAULT_ALLOCATION_DIMENSIONS)
    allocation = snapshot.get("allocation")
    allocation = allocation if isinstance(allocation, dict) else {}
    source_keys = dict(ALLOCATION_DIMENSIONS)

    dimensions: list[dict[str, str]] = []
    for dimension in selection:
        source_key = source_keys.get(dimension)
        if source_key is None:
            # Ordering-time validation rejects unsupported dimensions; this is
            # a defensive branch, not a reachable caller path.
            continue
        dimensions.append(
            {
                "dimension": dimension,
                "package_key": f"by_{dimension}",
                "posture": allocation_posture(allocation, source_key),
            }
        )
    return {
        "resolved_by": "caller_request" if requested else "report_default_policy",
        "dimensions": dimensions,
    }


def requested_allocation_dimensions(options: dict[str, Any]) -> list[str]:
    """The caller's selection, in the order asked for, de-duplicated."""

    requested = options.get("allocation_dimensions")
    if not isinstance(requested, list):
        return []
    supported = {dimension for dimension, _ in ALLOCATION_DIMENSIONS}
    ordered: list[str] = []
    for value in requested:
        if not isinstance(value, str):
            continue
        dimension = value.strip()
        if dimension in supported and dimension not in ordered:
            ordered.append(dimension)
    return ordered


def allocation_posture(allocation: dict[str, Any], source_key: str) -> str:
    if source_key not in allocation:
        return POSTURE_UNAVAILABLE
    buckets = allocation.get(source_key)
    if not isinstance(buckets, list):
        return POSTURE_UNAVAILABLE
    return POSTURE_READY if buckets else POSTURE_EMPTY
