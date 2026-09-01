"""Shaping lotus-performance contribution rows into Report's snapshot form.

Extracted from `reporting_read_service` (issue #209). These are pure functions
over one upstream payload - no service state, no I/O - and the read service had
grown past the source-size ratchet, so the cohesive unit this change touches is
the one that moves.
"""

from __future__ import annotations

from typing import Any


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
                "total_contribution_pct": row.get("total_contribution"),
                "average_weight_pct": row.get("average_weight"),
                "total_return_pct": row.get("total_return"),
                "local_contribution_pct": row.get("local_contribution"),
                "fx_contribution_pct": row.get("fx_contribution"),
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
                        "contribution_pct": row.get("contribution"),
                        "average_weight_pct": row.get("weight_avg"),
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
