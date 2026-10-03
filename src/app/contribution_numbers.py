"""Finite contribution evidence shared by source and retained presentation admission."""

from decimal import Decimal, InvalidOperation
from typing import Any


def contribution_decimal(value: object) -> Decimal | None:
    """Admit a finite source scalar without interpreting unknown as economic zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def admit_contribution_value(value: Any) -> Any:
    """Keep the exact finite source representation; withhold unusable evidence."""
    return value if contribution_decimal(value) is not None else None
