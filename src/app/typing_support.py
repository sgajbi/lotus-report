"""Narrowing a stored or computed value to a declared ``Literal`` member.

Several adapters read status columns as text and pass them into models whose
fields are ``Literal`` unions. The read is correct and the value is almost
always valid, but the type is ``str``, so the narrowing has to happen
somewhere. Doing it with a bare ``cast`` would assert the membership without
checking it, which is the shape of guard this repository has been removing:
a check satisfied by the presence of a value rather than by the value being
right.

``literal_value`` checks membership against the alias's own members and casts
only after the check has passed, so the cast is justified by the line above it
and the permitted set cannot drift from the alias it came from.
"""

from __future__ import annotations

from typing import Any, TypeVar, cast

T = TypeVar("T")


def literal_value(value: Any, allowed: tuple[T, ...], *, field: str) -> T:
    """Return ``value`` narrowed to ``T``, or raise naming the field and value.

    ``allowed`` is expected to be the alias's ``get_args(...)`` bound to an
    explicitly annotated tuple, which is what lets the return type follow the
    alias rather than collapsing to ``Any``.
    """

    if value in allowed:
        return cast(T, value)
    raise ValueError(f"{field} has unsupported value {value!r}; expected one of {allowed}")
