import pytest

from tests.integration.postgres_adapter_ownership import (
    own_postgres_adapter,
    postgres_adapter_scope,
)


class _RecordingAdapter:
    def __init__(self, name: str, closed: list[str]) -> None:
        self._name = name
        self._closed = closed

    def close(self) -> None:
        self._closed.append(self._name)


def test_postgres_adapter_scope_closes_owned_adapters_in_reverse_order() -> None:
    closed: list[str] = []

    with postgres_adapter_scope():
        first = own_postgres_adapter(_RecordingAdapter("first", closed))
        second = own_postgres_adapter(_RecordingAdapter("second", closed))
        assert first is not second

    assert closed == ["second", "first"]


def test_postgres_adapter_scope_closes_adapters_after_test_failure() -> None:
    closed: list[str] = []

    with pytest.raises(RuntimeError, match="proof failed"):
        with postgres_adapter_scope():
            own_postgres_adapter(_RecordingAdapter("owned", closed))
            raise RuntimeError("proof failed")

    assert closed == ["owned"]
