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
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
