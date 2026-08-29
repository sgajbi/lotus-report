from __future__ import annotations

from types import TracebackType
from typing import Self


class ManagedPostgresAdapter:
    """Context ownership for adapters whose close method respects provider ownership."""

    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()
