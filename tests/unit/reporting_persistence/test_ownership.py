import pytest

from app.reporting_persistence import ManagedPostgresAdapter


class _RecordingAdapter(ManagedPostgresAdapter):
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_managed_postgres_adapter_closes_after_success() -> None:
    adapter = _RecordingAdapter()

    with adapter as entered:
        assert entered is adapter

    assert adapter.close_count == 1


def test_managed_postgres_adapter_closes_after_failure() -> None:
    adapter = _RecordingAdapter()

    with pytest.raises(RuntimeError, match="proof failed"):
        with adapter:
            raise RuntimeError("proof failed")

    assert adapter.close_count == 1
