from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol, TypeVar


class _CloseablePostgresAdapter(Protocol):
    def close(self) -> None: ...


AdapterT = TypeVar("AdapterT", bound=_CloseablePostgresAdapter)

_owned_adapters: ContextVar[list[_CloseablePostgresAdapter] | None] = ContextVar(
    "owned_postgres_adapters",
    default=None,
)


@contextmanager
def postgres_adapter_scope() -> Iterator[None]:
    adapters: list[_CloseablePostgresAdapter] = []
    token = _owned_adapters.set(adapters)
    try:
        yield
    finally:
        for adapter in reversed(adapters):
            adapter.close()
        _owned_adapters.reset(token)


def own_postgres_adapter(adapter: AdapterT) -> AdapterT:
    adapters = _owned_adapters.get()
    if adapters is None:
        raise RuntimeError("postgres_adapter_scope_not_active")
    adapters.append(adapter)
    return adapter
