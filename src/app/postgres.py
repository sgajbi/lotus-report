from __future__ import annotations

import math
import time
from _thread import LockType
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Condition, Event, Lock, get_ident
from typing import Any, Iterator, Mapping

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from app.config import Settings, settings

_Connection = Connection[Mapping[str, Any]]
_RESCAN_SECONDS = 0.01


class PostgresConnectionPoolTimeoutError(TimeoutError):
    pass


class PostgresProviderDrainIncomplete(TimeoutError):
    """Admission is sealed; physical resources remain charged to this provider."""


@dataclass(eq=False)
class _ConnectionSlot:
    lock: LockType = field(default_factory=Lock)
    connection: _Connection | None = None
    state: str = "empty"
    generation: int = 0


@dataclass(eq=False)
class _StrictLease:
    provider: PostgresConnectionProvider
    slot: _ConnectionSlot
    generation: int
    thread_id: int
    deadline: float
    active: bool = True
    poisoned: bool = False

    @property
    def connection(self) -> _Connection:
        self.provider._validate_strict(self)
        return _slot_connection(self.slot)


class PostgresConnectionProvider:
    """One inventory; strict handles govern ownership, not native SQL timing.

    No production factory enables strict mode. Its lifecycle owner must bind an
    absolute shutdown deadline before enablement. Native interruption, physical
    close timing and the required cohort remain independently unqualified.
    """

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
        strict: bool = False,
        lifecycle_owner: object | None = None,
        shutdown_deadline: float | None = None,
    ) -> None:
        if min_size < 0:
            raise ValueError("postgres_pool_min_size_must_be_non_negative")
        if max_size < 1:
            raise ValueError("postgres_pool_max_size_must_be_positive")
        if min_size > max_size:
            raise ValueError("postgres_pool_min_size_cannot_exceed_max_size")
        if strict and (lifecycle_owner is None or shutdown_deadline is None):
            raise ValueError("postgres_strict_lifecycle_binding_required")
        if shutdown_deadline is not None:
            _remaining(shutdown_deadline)
        self.database_url = database_url
        self.min_size = min_size
        self.max_size = max_size
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.statement_timeout_ms = statement_timeout_ms
        self.application_name = application_name
        self._condition = Condition()
        self._closing = Event()
        self._drain_lock = Lock()
        self._slots = [_ConnectionSlot() for _ in range(max_size)]
        self._legacy: dict[int, _ConnectionSlot] = {}
        self._strict = strict
        self._lifecycle_owner = lifecycle_owner
        self._shutdown_deadline = shutdown_deadline
        self._open_minimum_connections()

    @classmethod
    def from_settings(cls, runtime_settings: Settings = settings) -> PostgresConnectionProvider:
        return cls(
            database_url=runtime_settings.report_job_ledger_database_url,
            min_size=runtime_settings.report_postgres_pool_min_size,
            max_size=runtime_settings.report_postgres_pool_max_size,
            acquire_timeout_seconds=runtime_settings.report_postgres_pool_acquire_timeout_seconds,
            connect_timeout_seconds=runtime_settings.report_postgres_connect_timeout_seconds,
            statement_timeout_ms=runtime_settings.report_postgres_statement_timeout_ms,
            application_name=runtime_settings.report_postgres_application_name,
        )

    @property
    def charged_connections(self) -> int:
        """Conservative snapshot: busy and unknown slots stay charged."""
        return sum(self._slot_charged(slot) for slot in self._slots)

    @staticmethod
    def _slot_charged(slot: _ConnectionSlot) -> bool:
        if not slot.lock.acquire(blocking=False):
            return True
        try:
            return slot.state not in {"empty", "closed"}
        finally:
            slot.lock.release()

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        connection = self.acquire()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self.release(connection)

    def acquire(self) -> _Connection:
        if self._strict:
            raise ValueError("postgres_strict_handle_required")
        slot = self._borrow(time.monotonic() + self.acquire_timeout_seconds, strict=False)
        return _slot_connection(slot)

    def _acquire_strict(self, *, deadline: float) -> _StrictLease:
        if not self._strict:
            raise ValueError("postgres_strict_lifecycle_binding_required")
        _remaining(deadline)
        slot = self._borrow(deadline, strict=True)
        try:
            _remaining(deadline)
            return _StrictLease(self, slot, slot.generation, get_ident(), deadline)
        except BaseException:
            self._terminal(slot, "idle")
            raise

    def _borrow(self, deadline: float, *, strict: bool) -> _ConnectionSlot:
        while True:
            if self._closing.is_set():
                raise RuntimeError("postgres_connection_provider_closed")
            timeout = _remaining(deadline) if strict else max(0, deadline - time.monotonic())
            if not self._condition.acquire(timeout=timeout):
                raise PostgresConnectionPoolTimeoutError("postgres_connection_pool_exhausted")
            try:
                if strict:
                    _remaining(deadline)
                if self._closing.is_set():
                    raise RuntimeError("postgres_connection_provider_closed")
                slot = self._reserve_slot(allow_create=not strict)
                if slot is None and not strict:
                    self._condition.wait(_remaining(deadline))
                    continue
            finally:
                self._condition.release()
            if slot is not None:
                return self._finish_borrow(slot, deadline, strict=strict)
            # Strict return never reacquires Condition, including to notify.
            # Rescan using the original budget; no hidden wait reacquisition.
            time.sleep(min(_RESCAN_SECONDS, _remaining(deadline)))

    def _reserve_slot(self, *, allow_create: bool) -> _ConnectionSlot | None:
        states = (("idle",), ("empty", "closed")) if allow_create else (("idle",),)
        for desired in states:
            for slot in self._slots:
                if not slot.lock.acquire(blocking=False):
                    continue
                if slot.state in desired:
                    slot.generation += 1
                    slot.state = "acquired" if desired == ("idle",) else "creating"
                    return slot
                slot.lock.release()
        return None

    def _finish_borrow(
        self, slot: _ConnectionSlot, deadline: float, *, strict: bool
    ) -> _ConnectionSlot:
        if slot.state != "creating" and _connection_closed(_slot_connection(slot)):
            self._terminal(slot, "closed")
            if not strict:
                self._notify_legacy_waiters()
            return self._borrow(deadline, strict=strict)
        try:
            if slot.state == "creating":
                slot.connection = None
                slot.connection = self._create_connection()
            if strict:
                # Reservation under Condition linearized before shutdown.
                # Do not reacquire that shared mutex while holding strict lease.
                return slot
            with self._condition:
                if self._closing.is_set():
                    raise RuntimeError("postgres_connection_provider_closed")
                slot.state = "acquired"
                self._legacy[id(_slot_connection(slot))] = slot
            return slot
        except BaseException:
            try:
                if slot.connection is None:
                    # Legacy connect retains its existing exception-cleanup contract.
                    self._terminal(slot, "empty")
                else:
                    self._dispose(slot)
            finally:
                if not strict:
                    self._notify_legacy_waiters()
            raise

    def release(self, connection: _Connection) -> None:
        """Legacy valid-caller API; raw connections are not generation tokens."""
        with self._condition:
            slot = self._legacy.pop(id(connection), None)
            if slot is None or slot.connection is not connection:
                raise ValueError("postgres_connection_not_leased")
            dispose = self._closing.is_set() or _connection_closed(connection)
            if not dispose:
                self._terminal(slot, "idle")
                self._condition.notify_all()
        if dispose:
            try:
                self._dispose(slot)
            finally:
                self._notify_legacy_waiters()

    def _notify_legacy_waiters(self) -> None:
        # Publish capacity before notifying; native disposal never holds Condition.
        with self._condition:
            self._condition.notify_all()

    def _validate_strict(self, lease: _StrictLease) -> None:
        if (
            lease.provider is not self
            or not lease.active
            or lease.generation != lease.slot.generation
            or lease.thread_id != get_ident()
        ):
            raise ValueError("postgres_strict_lease_owner_mismatch")

    def _release_strict(self, lease: _StrictLease, *, reusable: bool = False) -> None:
        self._validate_strict(lease)
        lease.active = False
        slot = lease.slot
        try:
            clean = (
                reusable and not lease.poisoned and not _connection_closed(_slot_connection(slot))
            )
        except BaseException:
            self._terminal(slot, "quarantined")
            raise
        if clean:
            self._terminal(slot, "idle")
            _remaining(lease.deadline)
            return
        try:
            _remaining(lease.deadline)
        except PostgresConnectionPoolTimeoutError:
            self._terminal(slot, "quarantined")
            raise
        self._dispose(slot)
        _remaining(lease.deadline)  # Detection only: no native preemption claim.

    @staticmethod
    def _terminal(slot: _ConnectionSlot, state: str) -> None:
        slot.state = state
        slot.lock.release()

    def _dispose(self, slot: _ConnectionSlot) -> None:
        slot.state = "closing"
        try:
            _close_connection(_slot_connection(slot))
            if not _connection_closed(_slot_connection(slot)):
                raise PostgresProviderDrainIncomplete("postgres_physical_close_unproved")
        except BaseException:
            self._terminal(slot, "quarantined")
            raise
        self._terminal(slot, "closed")

    def close(self, *, deadline: float | None = None) -> None:
        if self._strict:
            bound = self._shutdown_deadline
            if bound is None:
                raise ValueError("postgres_strict_lifecycle_binding_required")
            deadline = min(bound, deadline) if deadline is not None else bound
        self._seal(deadline)
        if deadline is None:
            self._close_idle_legacy()
            return
        try:
            if not self._drain_lock.acquire(timeout=_remaining(deadline)):
                raise PostgresProviderDrainIncomplete("postgres_shutdown_owner_busy")
            try:
                self._drain(deadline)
            finally:
                self._drain_lock.release()
        except Exception as exc:
            raise PostgresProviderDrainIncomplete("postgres_shutdown_incomplete") from exc

    def _seal(self, deadline: float | None) -> None:
        # Sticky admission gate also covers expiration before the shared barrier.
        self._closing.set()
        if deadline is None:
            self._condition.acquire()
        elif not self._condition.acquire(timeout=max(0, deadline - time.monotonic())):
            raise PostgresProviderDrainIncomplete("postgres_shutdown_barrier_incomplete")
        try:
            self._condition.notify_all()
        finally:
            self._condition.release()

    def _close_idle_legacy(self) -> None:
        for slot in self._slots:
            if slot.lock.acquire(blocking=False):
                if slot.state == "idle":
                    self._dispose(slot)
                else:
                    quarantined = slot.state == "quarantined"
                    slot.lock.release()
                    if quarantined:
                        raise PostgresProviderDrainIncomplete("postgres_physical_close_unproved")

    def _drain(self, deadline: float) -> None:
        while True:
            _remaining(deadline)
            pending = False
            for slot in self._slots:
                if not slot.lock.acquire(blocking=False):
                    pending = True
                    continue
                state = slot.state
                if state == "idle":
                    try:
                        _remaining(deadline)
                    except BaseException:
                        slot.lock.release()
                        raise
                    self._dispose(slot)
                else:
                    slot.lock.release()
                    if state == "quarantined":
                        raise PostgresProviderDrainIncomplete("postgres_physical_close_unproved")
                    pending |= state not in {"closed", "empty"}
            _remaining(deadline)
            if not pending:
                return
            time.sleep(min(_RESCAN_SECONDS, _remaining(deadline)))

    def _create_connection(self) -> _Connection:
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=self.connect_timeout_seconds,
            application_name=self.application_name,
            options=f"-c statement_timeout={self.statement_timeout_ms}",
        )

    def _open_minimum_connections(self) -> None:
        try:
            for slot in self._slots[: self.min_size]:
                slot.connection = self._create_connection()
                slot.state = "idle"
        except Exception:
            self.close()
            raise


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(deadline) or remaining <= 0:
        raise PostgresConnectionPoolTimeoutError("postgres_connection_pool_exhausted")
    return remaining


def _slot_connection(slot: _ConnectionSlot) -> _Connection:
    if slot.connection is None:
        raise RuntimeError("postgres_connection_slot_empty")
    return slot.connection


def _connection_closed(connection: _Connection) -> bool:
    return bool(getattr(connection, "closed", False))


def _close_connection(connection: _Connection) -> None:
    if not _connection_closed(connection):
        connection.close()


@lru_cache(maxsize=1)
def get_postgres_connection_provider() -> PostgresConnectionProvider:
    return PostgresConnectionProvider.from_settings(settings)


def close_postgres_connection_provider(*, deadline: float | None = None) -> None:
    """Never construct on shutdown; retain cached inventory on incomplete close."""
    if get_postgres_connection_provider.cache_info().currsize:
        provider = get_postgres_connection_provider()
        if deadline is None:
            provider.close()
        else:
            provider.close(deadline=deadline)
    get_postgres_connection_provider.cache_clear()
