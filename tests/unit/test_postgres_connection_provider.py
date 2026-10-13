import asyncio
import time
from dataclasses import replace
from threading import Event, Thread

import pytest

from app.postgres import (
    PostgresConnectionPoolTimeoutError,
    PostgresConnectionProvider,
    PostgresProviderDrainIncomplete,
)


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.events: list[str] = []

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.closed = True
        self.events.append("close")


def test_provider_reuses_idle_connections_and_applies_connection_options(monkeypatch):
    created: list[_FakeConnection] = []
    connect_kwargs: list[dict[str, object]] = []

    def _connect(*args, **kwargs):
        connect_kwargs.append({"args": args, **kwargs})
        connection = _FakeConnection()
        created.append(connection)
        return connection

    monkeypatch.setattr("app.postgres.psycopg.connect", _connect)

    provider = PostgresConnectionProvider(
        database_url="postgresql://report",
        max_size=2,
        connect_timeout_seconds=7,
        statement_timeout_ms=45000,
        application_name="lotus-report-test",
    )

    with provider.connection() as first:
        assert first is created[0]
    with provider.connection() as second:
        assert second is first

    assert len(created) == 1
    assert created[0].events == ["commit", "commit"]
    assert connect_kwargs[0]["args"] == ("postgresql://report",)
    assert connect_kwargs[0]["connect_timeout"] == 7
    assert connect_kwargs[0]["application_name"] == "lotus-report-test"
    assert connect_kwargs[0]["options"] == "-c statement_timeout=45000"


def test_provider_rolls_back_failed_units_of_work(monkeypatch):
    connection = _FakeConnection()

    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *args, **kwargs: connection)

    provider = PostgresConnectionProvider(
        database_url="postgresql://report",
        max_size=1,
    )

    with pytest.raises(RuntimeError, match="unit failed"):
        with provider.connection():
            raise RuntimeError("unit failed")

    assert connection.events == ["rollback"]
    with provider.connection() as reused:
        assert reused is connection


def test_provider_bounds_concurrent_connection_acquisition(monkeypatch):
    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *args, **kwargs: _FakeConnection())

    provider = PostgresConnectionProvider(
        database_url="postgresql://report",
        max_size=1,
        acquire_timeout_seconds=0,
    )

    first = provider.acquire()
    with pytest.raises(
        PostgresConnectionPoolTimeoutError,
        match="postgres_connection_pool_exhausted",
    ):
        provider.acquire()

    provider.release(first)


def test_provider_close_releases_idle_connections_and_blocks_reuse(monkeypatch):
    connection = _FakeConnection()

    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *args, **kwargs: connection)

    provider = PostgresConnectionProvider(database_url="postgresql://report", max_size=1)
    acquired = provider.acquire()
    provider.release(acquired)

    provider.close()

    assert connection.closed
    with pytest.raises(RuntimeError, match="postgres_connection_provider_closed"):
        provider.acquire()


def test_closing_an_uncached_provider_never_constructs_one(monkeypatch) -> None:
    """Shutdown must not open connections (issue #179 review chain).

    With an empty cache, close used to build a brand-new eagerly-connecting
    provider purely to shut it down - app teardown became a connection attempt
    against whatever DSN was configured.
    """
    from app import postgres

    postgres.get_postgres_connection_provider.cache_clear()
    monkeypatch.setattr(
        postgres.PostgresConnectionProvider,
        "from_settings",
        classmethod(
            lambda cls, _settings: (_ for _ in ()).throw(
                AssertionError("shutdown constructed a provider")
            )
        ),
    )

    postgres.close_postgres_connection_provider()


def _strict_provider(monkeypatch, connection=None, **kwargs):
    connection = connection or _FakeConnection()
    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *a, **kw: connection)
    provider = PostgresConnectionProvider(
        database_url="postgresql://report",
        max_size=1,
        strict=True,
        lifecycle_owner=object(),
        shutdown_deadline=time.monotonic() + 10,
        **kwargs,
    )
    return provider, connection


@pytest.mark.parametrize(
    "binding", [{}, {"lifecycle_owner": object()}, {"shutdown_deadline": float("inf")}]
)
def test_strict_requires_complete_lifecycle_binding_before_connect(monkeypatch, binding):
    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *a, **kw: pytest.fail("connect"))
    with pytest.raises(ValueError, match="lifecycle_binding"):
        PostgresConnectionProvider(database_url="postgresql://report", strict=True, **binding)


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), 0])
def test_strict_refuses_invalid_deadline_before_connect(monkeypatch, deadline):
    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *a, **kw: pytest.fail("connect"))
    with pytest.raises(PostgresConnectionPoolTimeoutError):
        PostgresConnectionProvider(
            database_url="postgresql://report",
            strict=True,
            lifecycle_owner=object(),
            shutdown_deadline=deadline,
        )


def test_strict_expired_admission_and_empty_inventory_do_not_connect(monkeypatch):
    provider, connection = _strict_provider(monkeypatch, min_size=0)
    with pytest.raises(PostgresConnectionPoolTimeoutError):
        provider._acquire_strict(deadline=0)
    with pytest.raises(PostgresConnectionPoolTimeoutError):
        provider._acquire_strict(deadline=time.monotonic() + 0.02)
    with pytest.raises(ValueError, match="strict_handle_required"):
        provider.acquire()
    assert provider.charged_connections == 0
    assert connection.events == []


def test_strict_return_does_not_wait_for_shared_condition(monkeypatch):
    provider, connection = _strict_provider(monkeypatch)
    lease = provider._acquire_strict(deadline=time.monotonic() + 1)
    held, release = Event(), Event()

    def hold_condition():
        with provider._condition:
            held.set()
            assert release.wait(2)

    thread = Thread(target=hold_condition)
    thread.start()
    assert held.wait(1)
    try:
        provider._release_strict(lease, reusable=True)
        assert connection.events == []
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    again = provider._acquire_strict(deadline=time.monotonic() + 1)
    assert again.generation > lease.generation
    with pytest.raises(ValueError, match="owner_mismatch"):
        provider._release_strict(replace(lease, active=True))
    with pytest.raises(ValueError, match="owner_mismatch"):
        provider._release_strict(lease)
    with pytest.raises(ValueError, match="owner_mismatch"):
        _ = lease.connection
    provider._release_strict(again)
    assert connection.events == ["close"]
    assert provider.charged_connections == 0


def test_wrong_thread_or_provider_cannot_return_strict_lease(monkeypatch):
    provider, _ = _strict_provider(monkeypatch)
    lease = provider._acquire_strict(deadline=time.monotonic() + 1)
    failures = []

    def wrong_owner():
        try:
            provider._release_strict(lease)
        except ValueError as exc:
            failures.append(str(exc))

    thread = Thread(target=wrong_owner)
    thread.start()
    thread.join(1)
    assert failures == ["postgres_strict_lease_owner_mismatch"]
    other, _ = _strict_provider(monkeypatch)
    with pytest.raises(ValueError, match="owner_mismatch"):
        other._release_strict(lease)
    assert lease.active
    assert provider.charged_connections == 1
    provider._release_strict(lease, reusable=True)
    with pytest.raises(ValueError, match="owner_mismatch"):
        provider._release_strict(lease)
    provider.close()
    other.close()


@pytest.mark.parametrize("error", [RuntimeError("close failed"), asyncio.CancelledError()])
def test_uncertain_close_quarantines_without_capacity_reuse(monkeypatch, error):
    class Uncertain(_FakeConnection):
        def close(self):
            self.events.append("close")
            raise error

    provider, connection = _strict_provider(monkeypatch, Uncertain())
    lease = provider._acquire_strict(deadline=time.monotonic() + 1)
    with pytest.raises(type(error)):
        provider._release_strict(lease)
    assert provider.charged_connections == 1
    with pytest.raises(PostgresProviderDrainIncomplete):
        provider.close()
    assert connection.events == ["close"]
    assert provider.charged_connections == 1


def test_close_that_returns_without_closed_proof_stays_charged(monkeypatch):
    class Unproved(_FakeConnection):
        def close(self):
            self.events.append("close")

    provider, connection = _strict_provider(monkeypatch, Unproved())
    with pytest.raises(PostgresProviderDrainIncomplete):
        provider.close()
    assert provider.charged_connections == 1
    with pytest.raises(PostgresProviderDrainIncomplete):
        provider.close()
    assert connection.events == ["close"]


def test_expired_discard_quarantines_without_starting_close(monkeypatch):
    provider, connection = _strict_provider(monkeypatch)
    lease = provider._acquire_strict(deadline=time.monotonic() + 1)
    lease.deadline = 0
    with pytest.raises(PostgresConnectionPoolTimeoutError):
        provider._release_strict(lease)
    assert connection.events == []
    assert provider.charged_connections == 1
    with pytest.raises(PostgresProviderDrainIncomplete):
        provider.close()


def test_incomplete_cached_drain_retains_inventory_and_seals_admission(monkeypatch):
    from app import postgres

    postgres.get_postgres_connection_provider.cache_clear()
    provider, connection = _strict_provider(monkeypatch)
    monkeypatch.setattr(
        PostgresConnectionProvider, "from_settings", classmethod(lambda cls, source: provider)
    )
    assert postgres.get_postgres_connection_provider() is provider
    lease = provider._acquire_strict(deadline=time.monotonic() + 1)
    try:
        with pytest.raises(PostgresProviderDrainIncomplete):
            postgres.close_postgres_connection_provider(deadline=time.monotonic() + 0.02)
        assert postgres.get_postgres_connection_provider() is provider
        assert provider.charged_connections == 1
        with pytest.raises(RuntimeError, match="provider_closed"):
            provider._acquire_strict(deadline=time.monotonic() + 1)
        provider._release_strict(lease, reusable=True)
        postgres.close_postgres_connection_provider()
        assert connection.events == ["close"]
        assert postgres.get_postgres_connection_provider.cache_info().currsize == 0
    finally:
        postgres.get_postgres_connection_provider.cache_clear()


def test_strict_shutdown_never_extends_bound_and_expired_close_seals(monkeypatch):
    provider, connection = _strict_provider(monkeypatch)
    provider._shutdown_deadline = 0
    with pytest.raises(PostgresProviderDrainIncomplete):
        provider.close(deadline=time.monotonic() + 100)
    assert connection.events == []
    assert provider.charged_connections == 1
    with pytest.raises(RuntimeError, match="provider_closed"):
        provider._acquire_strict(deadline=time.monotonic() + 1)


def test_foreground_drain_closes_returned_strict_lease_once(monkeypatch):
    provider, connection = _strict_provider(monkeypatch)
    lease = provider._acquire_strict(deadline=time.monotonic() + 1)
    errors = []

    def drain():
        try:
            provider.close()
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=drain)
    thread.start()
    assert provider._closing.wait(1)
    provider._release_strict(lease, reusable=True)
    thread.join(2)
    assert not thread.is_alive()
    assert errors == []
    assert connection.events == ["close"]
    assert provider.charged_connections == 0


def test_creation_finishing_after_shutdown_is_disposed_not_leased(monkeypatch):
    entered, finish = Event(), Event()
    connection = _FakeConnection()

    def connect(*a, **kw):
        entered.set()
        assert finish.wait(2)
        return connection

    monkeypatch.setattr("app.postgres.psycopg.connect", connect)
    provider = PostgresConnectionProvider(
        database_url="postgresql://report", min_size=0, max_size=1
    )
    errors = []

    def acquire():
        try:
            provider.acquire()
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = Thread(target=acquire)
    thread.start()
    assert entered.wait(1)
    try:
        provider.close()
        assert provider.charged_connections == 1
    finally:
        finish.set()
        thread.join(2)
    assert not thread.is_alive()
    assert errors == ["postgres_connection_provider_closed"]
    assert connection.events == ["close"]
    assert provider.charged_connections == 0


def test_waiter_refuses_shutdown_and_legacy_owner_closes_once(monkeypatch):
    provider, connection = _strict_provider(monkeypatch)
    # Legacy path remains independently callable on a legacy instance only.
    provider._strict = False
    acquired = provider.acquire()
    waiting, errors = Event(), []
    original = provider._reserve_slot

    def reserve(**kwargs):
        result = original(**kwargs)
        if result is None:
            waiting.set()
        return result

    monkeypatch.setattr(provider, "_reserve_slot", reserve)

    def waiter():
        try:
            provider.acquire()
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = Thread(target=waiter)
    thread.start()
    assert waiting.wait(1)
    provider.close()
    provider.release(acquired)
    thread.join(1)
    assert not thread.is_alive()
    assert errors == ["postgres_connection_provider_closed"]
    assert connection.events == ["close"]
    with pytest.raises(ValueError, match="not_leased"):
        provider.release(acquired)
    assert provider.charged_connections == 0


def test_native_close_runs_outside_shared_condition(monkeypatch):
    entered, finish = Event(), Event()

    class BlockingClose(_FakeConnection):
        def close(self):
            entered.set()
            assert finish.wait(2)
            super().close()

    provider, connection = _strict_provider(monkeypatch, BlockingClose())
    provider._strict = False
    thread = Thread(target=provider.close)
    thread.start()
    assert entered.wait(1)
    try:
        assert provider._condition.acquire(timeout=0.1)
        provider._condition.release()
        assert provider.charged_connections == 1
    finally:
        finish.set()
        thread.join(2)
    assert not thread.is_alive()
    assert connection.events == ["close"]


def test_legacy_connect_failure_returns_capacity_for_retry(monkeypatch):
    attempts = []
    connection = _FakeConnection()

    def connect(*a, **kw):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("connect failed")
        return connection

    monkeypatch.setattr("app.postgres.psycopg.connect", connect)
    provider = PostgresConnectionProvider(
        database_url="postgresql://report", min_size=0, max_size=1, acquire_timeout_seconds=0
    )
    with pytest.raises(RuntimeError, match="connect failed"):
        provider.acquire()
    assert provider.charged_connections == 0
    assert provider.acquire() is connection
    provider.release(connection)
    provider.close()
    assert len(attempts) == 2
    assert connection.events == ["close"]


def _start_legacy_waiter(provider, monkeypatch):
    waiting, done = Event(), Event()
    waits, outcomes = [], []
    native_wait = provider._condition.wait

    def wait(timeout=None):
        waits.append(timeout)
        waiting.set()
        return native_wait(timeout)

    monkeypatch.setattr(provider._condition, "wait", wait)

    def acquire():
        try:
            connection = provider.acquire()
            outcomes.append(connection)
            provider.release(connection)
        except BaseException as exc:
            outcomes.append(exc)
        finally:
            done.set()

    thread = Thread(target=acquire)
    thread.start()
    return thread, waiting, done, waits, outcomes


def test_legacy_waiter_uses_condition_notification_on_reusable_return(monkeypatch):
    connection = _FakeConnection()
    monkeypatch.setattr("app.postgres.psycopg.connect", lambda *a, **kw: connection)
    provider = PostgresConnectionProvider(database_url="postgresql://report", max_size=1)
    acquired = provider.acquire()
    thread, waiting, done, waits, outcomes = _start_legacy_waiter(provider, monkeypatch)
    try:
        assert waiting.wait(1)
        provider.release(acquired)
        assert done.wait(1)
        assert outcomes == [connection]
        assert len(waits) == 1
        assert waits[0] > 1  # Woke by notification, before the original timeout.
    finally:
        provider.close()
        thread.join(2)
    assert not thread.is_alive()


def test_legacy_waiter_is_notified_after_disposal_publishes_capacity(monkeypatch):
    created = []

    def connect(*a, **kw):
        connection = _FakeConnection()
        created.append(connection)
        return connection

    monkeypatch.setattr("app.postgres.psycopg.connect", connect)
    provider = PostgresConnectionProvider(database_url="postgresql://report", max_size=1)
    acquired = provider.acquire()
    acquired.closed = True
    disposing, finish = Event(), Event()
    native_dispose = provider._dispose

    def dispose(slot):
        disposing.set()
        assert finish.wait(2)
        native_dispose(slot)

    monkeypatch.setattr(provider, "_dispose", dispose)
    thread, waiting, done, waits, outcomes = _start_legacy_waiter(provider, monkeypatch)
    release_errors = []

    def release():
        try:
            provider.release(acquired)
        except BaseException as exc:
            release_errors.append(exc)

    returning = Thread(target=release)
    try:
        assert waiting.wait(1)
        returning.start()
        assert disposing.wait(1)
        assert provider.charged_connections == 1
        assert not done.is_set()
        finish.set()
        assert done.wait(1)
        returning.join(1)
        assert release_errors == []
        assert len(created) == 2
        assert outcomes == [created[1]]
        assert len(waits) == 1
    finally:
        finish.set()
        provider.close()
        thread.join(2)
        if returning.ident is not None:
            returning.join(2)
    assert not thread.is_alive() and not returning.is_alive()


def test_legacy_waiter_is_notified_after_failed_creation_frees_capacity(monkeypatch):
    connecting, fail = Event(), Event()
    connection = _FakeConnection()
    attempts = []

    def connect(*a, **kw):
        attempts.append(1)
        if len(attempts) == 1:
            connecting.set()
            assert fail.wait(2)
            raise RuntimeError("connect failed")
        return connection

    monkeypatch.setattr("app.postgres.psycopg.connect", connect)
    provider = PostgresConnectionProvider(
        database_url="postgresql://report", min_size=0, max_size=1
    )
    failures = []

    def acquire():
        try:
            provider.acquire()
        except BaseException as exc:
            failures.append(exc)

    creator = Thread(target=acquire)
    creator.start()
    assert connecting.wait(1)
    thread, waiting, done, waits, outcomes = _start_legacy_waiter(provider, monkeypatch)
    try:
        assert waiting.wait(1)
        fail.set()
        assert done.wait(1)
        creator.join(1)
        assert len(failures) == 1 and str(failures[0]) == "connect failed"
        assert outcomes == [connection]
        assert len(attempts) == 2
        assert len(waits) == 1
    finally:
        fail.set()
        provider.close()
        creator.join(2)
        thread.join(2)
    assert not creator.is_alive() and not thread.is_alive()


@pytest.mark.parametrize("health_finished", [100.5, 101.0, 102.0])
def test_strict_health_inspection_must_finish_before_lease_deadline(monkeypatch, health_finished):
    from app import postgres

    clock, inspect = [100.0], [False]
    monkeypatch.setattr(postgres.time, "monotonic", lambda: clock[0])

    class DelayedHealth:
        @property
        def closed(self):
            if inspect[0]:
                clock[0] = health_finished
            return False

    provider, connection = _strict_provider(monkeypatch, DelayedHealth())
    inspect[0] = True
    if health_finished < 101.0:
        lease = provider._acquire_strict(deadline=101.0)
        assert lease.connection is connection
        provider._release_strict(lease, reusable=True)
    else:
        with pytest.raises(PostgresConnectionPoolTimeoutError):
            provider._acquire_strict(deadline=101.0)
        # Expired publication returns the untouched slot to its inventory.
        assert provider._slots[0].state == "idle"
        assert provider._slots[0].lock.acquire(blocking=False)
        provider._slots[0].lock.release()
    assert provider.charged_connections == 1
    inspect[0], clock[0] = False, 100.0
    again = provider._acquire_strict(deadline=101.0)
    assert again.connection is connection
    provider._release_strict(again, reusable=True)


def test_strict_admission_bounds_shared_condition_contention(monkeypatch):
    provider, connection = _strict_provider(monkeypatch)
    held, release = Event(), Event()

    def hold():
        with provider._condition:
            held.set()
            assert release.wait(2)

    thread = Thread(target=hold)
    thread.start()
    assert held.wait(1)
    try:
        with pytest.raises(PostgresConnectionPoolTimeoutError):
            provider._acquire_strict(deadline=time.monotonic() + 0.02)
        with pytest.raises(PostgresProviderDrainIncomplete, match="barrier_incomplete"):
            provider.close(deadline=time.monotonic() + 0.02)
        assert provider._closing.is_set()
        assert connection.events == []
    finally:
        release.set()
        thread.join(2)
    provider.close()
    assert connection.events == ["close"]


def test_native_close_overrun_is_detected_after_return_without_recharging(monkeypatch):
    from app import postgres

    clock = [100.0]
    monkeypatch.setattr(postgres.time, "monotonic", lambda: clock[0])

    class LateClose(_FakeConnection):
        def close(self):
            clock[0] = 102.0
            super().close()

    provider, connection = _strict_provider(monkeypatch, LateClose())
    lease = provider._acquire_strict(deadline=101.0)
    with pytest.raises(PostgresConnectionPoolTimeoutError):
        provider._release_strict(lease)
    assert connection.events == ["close"]
    assert provider.charged_connections == 0
    provider.close()


@pytest.mark.parametrize("error", [RuntimeError("close failed"), asyncio.CancelledError()])
@pytest.mark.parametrize("strict", [False, True])
def test_cached_provider_survives_close_failure_and_cancellation(monkeypatch, error, strict):
    from app import postgres

    class Uncertain(_FakeConnection):
        def close(self):
            self.events.append("close")
            raise error

    postgres.get_postgres_connection_provider.cache_clear()
    provider, connection = _strict_provider(monkeypatch, Uncertain())
    provider._strict = strict
    monkeypatch.setattr(
        PostgresConnectionProvider, "from_settings", classmethod(lambda cls, source: provider)
    )
    assert postgres.get_postgres_connection_provider() is provider
    try:
        expected = (
            PostgresProviderDrainIncomplete
            if strict and isinstance(error, Exception)
            else type(error)
        )
        with pytest.raises(expected):
            postgres.close_postgres_connection_provider()
        assert postgres.get_postgres_connection_provider() is provider
        assert provider.charged_connections == 1
        assert connection.events == ["close"]
        with pytest.raises(PostgresProviderDrainIncomplete):
            postgres.close_postgres_connection_provider()
        assert postgres.get_postgres_connection_provider() is provider
        assert connection.events == ["close"]
    finally:
        postgres.get_postgres_connection_provider.cache_clear()
