from contextlib import contextmanager
from types import SimpleNamespace

from app import runtime_schema


class _RecordingConnection:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls

    def execute(self, sql: str, params: object) -> None:
        self._calls.append((sql, params))

    def commit(self) -> None:
        self._calls.append(("commit", None))

    def rollback(self) -> None:
        self._calls.append(("rollback", None))


def test_runtime_schema_lock_uses_postgres_advisory_lock_and_always_releases(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class _Provider:
        @contextmanager
        def connection(self):
            calls.append(("acquire", None))
            connection = _RecordingConnection(calls)
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                calls.append(("release", None))

    with runtime_schema._runtime_schema_lock(_Provider()):
        calls.append(("inside", None))

    lock_params = (runtime_schema._SCHEMA_LOCK_KEY,)
    assert calls == [
        ("acquire", None),
        ("SELECT pg_advisory_lock(%s)", lock_params),
        ("commit", None),
        ("inside", None),
        ("SELECT pg_advisory_unlock(%s)", lock_params),
        ("commit", None),
        ("commit", None),
        ("release", None),
    ]


def test_runtime_schema_lock_releases_connection_when_schema_init_fails(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _Provider:
        @contextmanager
        def connection(self):
            calls.append(("acquire", None))
            connection = _RecordingConnection(calls)
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                calls.append(("release", None))

    try:
        with runtime_schema._runtime_schema_lock(_Provider()):
            raise RuntimeError("schema init failed")
    except RuntimeError as exc:
        assert str(exc) == "schema init failed"

    assert ("SELECT pg_advisory_unlock(%s)", (runtime_schema._SCHEMA_LOCK_KEY,)) in calls
    assert ("rollback", None) in calls
    assert calls[-1] == ("release", None)


def test_ensure_runtime_schema_checks_batch_and_snapshot_stores_under_lock(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _Provider:
        @classmethod
        def from_settings(cls, settings) -> "_Provider":
            calls.append(("provider_from_settings", settings.report_job_ledger_database_url))
            return cls()

        def close(self) -> None:
            calls.append(("provider_close", None))

    class _BatchLedger:
        def __init__(self, *, connection_provider: _Provider) -> None:
            calls.append(("batch_init", connection_provider.__class__.__name__))

        def check_ready(self) -> None:
            calls.append(("batch_ready", None))

    class _SnapshotStore:
        def __init__(self, *, connection_provider: _Provider) -> None:
            calls.append(("snapshot_init", connection_provider.__class__.__name__))

        def check_ready(self) -> None:
            calls.append(("snapshot_ready", None))

    class _Lock:
        def __init__(self, connection_provider: _Provider) -> None:
            self._connection_provider = connection_provider

        def __enter__(self) -> None:
            calls.append(("lock_enter", self._connection_provider.__class__.__name__))

        def __exit__(self, exc_type, exc, tb) -> None:
            calls.append(("lock_exit", self._connection_provider.__class__.__name__))

    monkeypatch.setattr(
        runtime_schema,
        "settings",
        SimpleNamespace(report_job_ledger_database_url="postgresql://report-runtime"),
    )
    monkeypatch.setattr(runtime_schema, "PostgresConnectionProvider", _Provider)
    monkeypatch.setattr(runtime_schema, "_runtime_schema_lock", _Lock)
    monkeypatch.setattr(runtime_schema, "PostgresReportBatchLedger", _BatchLedger)
    monkeypatch.setattr(runtime_schema, "PostgresReportInputSnapshotStore", _SnapshotStore)

    runtime_schema.ensure_runtime_schema()

    assert calls == [
        ("provider_from_settings", "postgresql://report-runtime"),
        ("lock_enter", "_Provider"),
        ("batch_init", "_Provider"),
        ("batch_ready", None),
        ("snapshot_init", "_Provider"),
        ("snapshot_ready", None),
        ("lock_exit", "_Provider"),
        ("provider_close", None),
    ]
