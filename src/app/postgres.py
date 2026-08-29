from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from functools import lru_cache
from threading import Condition
from typing import Any, Iterator, Mapping

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from app.config import Settings, settings


class PostgresConnectionPoolTimeoutError(TimeoutError):
    pass


class PostgresConnectionProvider:
    """Small bounded psycopg connection provider shared by runtime PostgreSQL adapters."""

    def __init__(
        self,
        *,
        database_url: str,
        min_size: int = 1,
        max_size: int = 10,
        acquire_timeout_seconds: int = 5,
        connect_timeout_seconds: int = 5,
        statement_timeout_ms: int = 30000,
        application_name: str = "lotus-report",
    ) -> None:
        if min_size < 0:
            raise ValueError("postgres_pool_min_size_must_be_non_negative")
        if max_size < 1:
            raise ValueError("postgres_pool_max_size_must_be_positive")
        if min_size > max_size:
            raise ValueError("postgres_pool_min_size_cannot_exceed_max_size")
        self.database_url = database_url
        self.min_size = min_size
        self.max_size = max_size
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.statement_timeout_ms = statement_timeout_ms
        self.application_name = application_name
        self._condition = Condition()
        self._idle: deque[Connection[Mapping[str, Any]]] = deque()
        self._created = 0
        self._closed = False
        self._open_minimum_connections()

    @classmethod
    def from_settings(cls, runtime_settings: Settings = settings) -> "PostgresConnectionProvider":
        return cls(
            database_url=runtime_settings.report_job_ledger_database_url,
            min_size=runtime_settings.report_postgres_pool_min_size,
            max_size=runtime_settings.report_postgres_pool_max_size,
            acquire_timeout_seconds=runtime_settings.report_postgres_pool_acquire_timeout_seconds,
            connect_timeout_seconds=runtime_settings.report_postgres_connect_timeout_seconds,
            statement_timeout_ms=runtime_settings.report_postgres_statement_timeout_ms,
            application_name=runtime_settings.report_postgres_application_name,
        )

    @contextmanager
    def connection(self) -> Iterator[Connection[Mapping[str, Any]]]:
        connection = self.acquire()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self.release(connection)

    def acquire(self) -> Connection[Mapping[str, Any]]:
        deadline = time.monotonic() + self.acquire_timeout_seconds
        while True:
            with self._condition:
                if self._closed:
                    raise RuntimeError("postgres_connection_provider_closed")
                while self._idle:
                    connection = self._idle.pop()
                    if not _connection_closed(connection):
                        return connection
                    self._created -= 1
                if self._created < self.max_size:
                    self._created += 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PostgresConnectionPoolTimeoutError("postgres_connection_pool_exhausted")
                self._condition.wait(remaining)

        try:
            return self._create_connection()
        except Exception:
            with self._condition:
                self._created -= 1
                self._condition.notify()
            raise

    def release(self, connection: Connection[Mapping[str, Any]]) -> None:
        with self._condition:
            if self._closed or _connection_closed(connection):
                self._created -= 1
                _close_connection(connection)
            else:
                self._idle.append(connection)
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            idle = list(self._idle)
            self._idle.clear()
            self._created -= len(idle)
            self._condition.notify_all()
        for connection in idle:
            _close_connection(connection)

    def _create_connection(self) -> Connection[Mapping[str, Any]]:
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=self.connect_timeout_seconds,
            application_name=self.application_name,
            options=f"-c statement_timeout={self.statement_timeout_ms}",
        )

    def _open_minimum_connections(self) -> None:
        try:
            for _ in range(self.min_size):
                self._idle.append(self._create_connection())
                self._created += 1
        except Exception:
            self.close()
            raise


def _connection_closed(connection: Connection[Mapping[str, Any]]) -> bool:
    return bool(getattr(connection, "closed", False))


def _close_connection(connection: Connection[Mapping[str, Any]]) -> None:
    if not _connection_closed(connection):
        connection.close()


@lru_cache(maxsize=1)
def get_postgres_connection_provider() -> PostgresConnectionProvider:
    return PostgresConnectionProvider.from_settings(settings)


def close_postgres_connection_provider() -> None:
    """Close the cached provider if one exists; never construct one to close it.

    Shutdown must not open connections: with an empty cache this used to build a
    brand-new provider - eagerly connecting - purely to shut it down, which turned
    app teardown into a connection attempt against whatever DSN was configured.
    """
    if get_postgres_connection_provider.cache_info().currsize:
        get_postgres_connection_provider().close()
    get_postgres_connection_provider.cache_clear()
