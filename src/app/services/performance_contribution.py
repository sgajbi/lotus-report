"""Shaping lotus-performance contribution rows into Report's snapshot form.

Extracted from `reporting_read_service` (issue #209). These are pure functions
over one upstream payload - no service state, no I/O - and the read service had
grown past the source-size ratchet, so the cohesive unit this change touches is
the one that moves.
"""

from __future__ import annotations

from typing import Any

from app.contribution_numbers import admit_contribution_value, contribution_decimal


def select_contribution_extreme(
    rows: list[object], *, largest: bool, positive: bool | None = None
) -> dict[str, Any] | None:
    """Select a finite extreme, optionally restricted to the labelled sign."""

    mapped_rows = [_as_dict(row) for row in rows]
    admitted_rows = [
        (value, row)
        for row in mapped_rows
        if (value := contribution_decimal(row.get("total_contribution_pct"))) is not None
        and (positive is None or (value > 0 if positive else value < 0))
    ]
    if not admitted_rows:
        return None
    select = max if largest else min
    selected = select(admitted_rows, key=lambda item: item[0])[1]
    return {
        "security_id": selected.get("security_id"),
        "position_id": selected.get("position_id"),
        "total_contribution_pct": selected.get("total_contribution_pct"),
        "average_weight_pct": admit_contribution_value(selected.get("average_weight_pct")),
        "total_return_pct": admit_contribution_value(selected.get("total_return_pct")),
    }


def map_position_contributions(rows: list[Any]) -> list[dict[str, Any]]:
    """Position-level contribution rows, keyed by the security they belong to."""

    mapped: list[dict[str, Any]] = []
    for row_payload in rows:
        row = _as_dict(row_payload)
        position_id = _safe_str(row.get("position_id"))
        mapped.append(
            {
                "position_id": position_id,
                "security_id": security_id_from_position_id(position_id),
                "total_contribution_pct": admit_contribution_value(row.get("total_contribution")),
                "average_weight_pct": admit_contribution_value(row.get("average_weight")),
                "total_return_pct": admit_contribution_value(row.get("total_return")),
                "local_contribution_pct": admit_contribution_value(row.get("local_contribution")),
                "fx_contribution_pct": admit_contribution_value(row.get("fx_contribution")),
            }
        )
    return mapped


def map_contribution_levels(
    levels: list[Any],
    *,
    to_int: Any,
) -> list[dict[str, Any]]:
    """Hierarchy levels, preserved as the source grouped them.

    A hierarchy decomposes the period (levels sum to the total) where the
    position ranking selects from it, so the two are shaped separately and
    never merged into one visual.
    """

    mapped: list[dict[str, Any]] = []
    for level_payload in levels:
        level = _as_dict(level_payload)
        mapped.append(
            {
                "level": to_int(level.get("level")),
                "name": level.get("name"),
                "parent": level.get("parent"),
                "rows": [
                    {
                        "key": _as_dict(row.get("key")),
                        "contribution_pct": admit_contribution_value(row.get("contribution")),
                        "average_weight_pct": admit_contribution_value(row.get("weight_avg")),
                        "is_other": row.get("is_other"),
                        "children_count": row.get("children_count"),
                    }
                    for row in [_as_dict(item) for item in _as_list(level.get("rows"))]
                ],
            }
        )
    return mapped


def security_id_from_position_id(position_id: str) -> str:
    if ":" in position_id:
        return position_id.rsplit(":", 1)[-1]
    return position_id


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
