"""Deterministic failure doubles preserve charged provider inventory."""

import time

import pytest

from app.postgres import (
    PostgresConnectionProvider,
    PostgresProviderDrainIncomplete,
    _slot_connection,
)
from tests.unit.test_postgres_connection_provider import _FakeConnection, _strict_provider


@pytest.mark.parametrize(
    "limits", [{"min_size": -1}, {"max_size": 0}, {"min_size": 2, "max_size": 1}]
)
def test_invalid_inventory_limits_refuse_before_native_connect(monkeypatch, limits):
    monkeypatch.setattr(
        "app.postgres.psycopg.connect", lambda *a, **k: pytest.fail("native connect")
    )
    with pytest.raises(ValueError, match="postgres_pool"):
        PostgresConnectionProvider(database_url="postgresql://test", **limits)


def test_legacy_provider_refuses_strict_handle_and_empty_slot(monkeypatch):
    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *a, **k: _FakeConnection())
    provider = PostgresConnectionProvider(database_url="postgresql://test", min_size=0, max_size=1)
    with pytest.raises(ValueError, match="strict_lifecycle_binding_required"):
        provider._acquire_strict(deadline=time.monotonic() + 1)
    with pytest.raises(RuntimeError, match="slot_empty"):
        _slot_connection(provider._slots[0])
    provider.close()


def test_creation_failure_publishes_empty_capacity_before_retry(monkeypatch):
    connection = _FakeConnection()
    attempts = 0

    def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("native connect failed")
        return connection

    monkeypatch.setattr("app.postgres.psycopg.connect", connect)
    provider = PostgresConnectionProvider(database_url="postgresql://test", min_size=0, max_size=1)
    with pytest.raises(OSError, match="native connect failed"):
        provider.acquire()
    assert provider.charged_connections == 0
    acquired = provider.acquire()
    assert acquired is connection and attempts == 2
    provider.release(acquired)
    provider.close()


def test_closed_idle_connection_is_replaced_only_in_legacy_mode(monkeypatch):
    connections = [_FakeConnection(), _FakeConnection()]
    calls = iter(connections)
    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *a, **k: next(calls))
    provider = PostgresConnectionProvider(database_url="postgresql://test", max_size=1)
    connections[0].closed = True
    acquired = provider.acquire()
    assert acquired is connections[1]
    provider.release(acquired)
    provider.close()


def test_uncertain_physical_close_remains_charged_and_legacy_drain_refuses(monkeypatch):
    class Unproved(_FakeConnection):
        def close(self):
            self.events.append("close")

    provider, connection = _strict_provider(monkeypatch, Unproved())
    lease = provider._acquire_strict(deadline=time.monotonic() + 1)
    with pytest.raises(PostgresProviderDrainIncomplete, match="physical_close_unproved"):
        provider._release_strict(lease)
    assert provider.charged_connections == 1
    provider._strict = False
    with pytest.raises(PostgresProviderDrainIncomplete, match="physical_close_unproved"):
        provider.close()
    assert connection.events == ["close"]


def test_health_failure_on_return_quarantines_without_reusing_capacity(monkeypatch):
    class HealthFailure:
        fail = False

        @property
        def closed(self):
            if self.fail:
                raise OSError("health unavailable")
            return False

    connection = HealthFailure()
    provider, _ = _strict_provider(monkeypatch, connection)
    lease = provider._acquire_strict(deadline=time.monotonic() + 1)
    connection.fail = True
    with pytest.raises(OSError, match="health unavailable"):
        provider._release_strict(lease, reusable=True)
    assert not lease.active
    assert provider._slots[0].state == "quarantined"
    assert provider.charged_connections == 1


def test_competing_shutdown_owner_refuses_then_retry_drains_same_inventory(monkeypatch):
    provider, connection = _strict_provider(monkeypatch)
    provider._drain_lock.acquire()
    try:
        with pytest.raises(PostgresProviderDrainIncomplete) as caught:
            provider.close(deadline=time.monotonic() + 0.01)
        assert str(caught.value.__cause__) == "postgres_shutdown_owner_busy"
        assert provider.charged_connections == 1
    finally:
        provider._drain_lock.release()
    provider.close(deadline=time.monotonic() + 1)
    assert connection.closed and provider.charged_connections == 0


def test_expiry_before_idle_disposal_releases_slot_lock_for_later_drain(monkeypatch):
    provider, connection = _strict_provider(monkeypatch)
    with monkeypatch.context() as controlled:
        ticks = iter([0, 0, 0, 2])
        controlled.setattr("app.postgres.time.monotonic", lambda: next(ticks))
        with pytest.raises(PostgresProviderDrainIncomplete):
            provider.close(deadline=1)
    slot = provider._slots[0]
    assert slot.state == "idle" and not slot.lock.locked()
    assert provider.charged_connections == 1 and connection.closed is False
    provider.close(deadline=time.monotonic() + 1)
    assert connection.closed


def test_minimum_inventory_creation_failure_closes_previously_opened_slot(monkeypatch):
    connection = _FakeConnection()
    attempts = 0

    def connect(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise OSError("second native connect failed")
        return connection

    monkeypatch.setattr("app.postgres.psycopg.connect", connect)
    with pytest.raises(OSError, match="second native connect failed"):
        PostgresConnectionProvider(database_url="postgresql://test", min_size=2, max_size=2)
    assert connection.events == ["close"] and connection.closed
