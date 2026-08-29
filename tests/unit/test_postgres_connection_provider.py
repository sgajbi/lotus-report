import pytest

from app.postgres import PostgresConnectionPoolTimeoutError, PostgresConnectionProvider


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
