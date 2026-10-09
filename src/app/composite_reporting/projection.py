"""Complete retained source projections, including historical method ordering."""

from copy import deepcopy
from typing import Any

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.table_builder import build_composite_tables


def require_complete_projection(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    normalized = deepcopy(actual)
    _normalize_method_order(normalized, expected)
    if (
        normalized["report_facts"] != expected["report_facts"]
        or normalized["tables"] != expected["tables"]
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_PROJECTION_CONFLICT")


def _normalize_method_order(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    retained = actual["report_facts"].get("Methods")
    required = expected["report_facts"]["Methods"]
    if retained == required:
        return
    # Older retained Methods rows follow source object insertion order. Only
    # a complete permutation of the same source paths is equivalent; ordinal
    # field pointers and row IDs must still bind their original retained list.
    if (
        not isinstance(retained, list)
        or any(not isinstance(item, str) for item in retained)
        or sorted(retained) != sorted(required)
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_PROJECTION_CONFLICT")
    table = next((t for t in actual["tables"] if t["table_id"] == "Methods"), None)
    expected_table = next(t for t in expected["tables"] if t["table_id"] == "Methods")
    if table is None or len(table["rows"]) != len(retained):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_PROJECTION_CONFLICT")
    rows = []
    for index, pointer in enumerate(required):
        old_index = retained.index(pointer)
        row = table["rows"][old_index]
        field = row["cells"].get("field", {})
        if (
            row["row_id"] != str(old_index)
            or field.get("source_pointer") != f"/report_facts/Methods/{old_index}"
            or field.get("canonical_value") != pointer
        ):
            raise CompositeEvidenceRefused("COMPOSITE_REPORT_PROJECTION_CONFLICT")
        row["row_id"] = str(index)
        field["source_pointer"] = f"/report_facts/Methods/{index}"
        rows.append(row)
    table["rows"] = rows
    actual["report_facts"]["Methods"] = required
    if table != expected_table:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_PROJECTION_CONFLICT")


def require_complete_primary_projection(actual: dict[str, Any]) -> None:
    require_complete_projection(actual, build_composite_tables(actual))
