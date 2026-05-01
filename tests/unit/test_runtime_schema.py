from types import SimpleNamespace

from app import runtime_schema


class _RecordingConnection:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls

    def execute(self, sql: str, params: object) -> None:
        self._calls.append((sql, params))

    def commit(self) -> None:
        self._calls.append(("commit", None))

    def close(self) -> None:
        self._calls.append(("close", None))


def test_runtime_schema_lock_uses_postgres_advisory_lock_and_always_releases(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def _connect(database_url: str) -> _RecordingConnection:
        calls.append(("connect", database_url))
        return _RecordingConnection(calls)

    monkeypatch.setattr(runtime_schema.psycopg, "connect", _connect)

    with runtime_schema._runtime_schema_lock("postgresql://report-runtime"):
        calls.append(("inside", None))

    lock_params = (runtime_schema._SCHEMA_LOCK_KEY,)
    assert calls == [
        ("connect", "postgresql://report-runtime"),
        ("SELECT pg_advisory_lock(%s)", lock_params),
        ("commit", None),
        ("inside", None),
        ("SELECT pg_advisory_unlock(%s)", lock_params),
        ("commit", None),
        ("close", None),
    ]


def test_runtime_schema_lock_releases_connection_when_schema_init_fails(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def _connect(database_url: str) -> _RecordingConnection:
        calls.append(("connect", database_url))
        return _RecordingConnection(calls)

    monkeypatch.setattr(runtime_schema.psycopg, "connect", _connect)

    try:
        with runtime_schema._runtime_schema_lock("postgresql://report-runtime"):
            raise RuntimeError("schema init failed")
    except RuntimeError as exc:
        assert str(exc) == "schema init failed"

    assert ("SELECT pg_advisory_unlock(%s)", (runtime_schema._SCHEMA_LOCK_KEY,)) in calls
    assert calls[-1] == ("close", None)


def test_ensure_runtime_schema_checks_batch_and_snapshot_stores_under_lock(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    class _BatchLedger:
        def __init__(self, database_url: str) -> None:
            calls.append(("batch_init", database_url))

        def check_ready(self) -> None:
            calls.append(("batch_ready", None))

    class _SnapshotStore:
        def __init__(self, database_url: str) -> None:
            calls.append(("snapshot_init", database_url))

        def check_ready(self) -> None:
            calls.append(("snapshot_ready", None))

    class _Lock:
        def __init__(self, database_url: str) -> None:
            self._database_url = database_url

        def __enter__(self) -> None:
            calls.append(("lock_enter", self._database_url))

        def __exit__(self, exc_type, exc, tb) -> None:
            calls.append(("lock_exit", self._database_url))

    monkeypatch.setattr(
        runtime_schema,
        "settings",
        SimpleNamespace(report_job_ledger_database_url="postgresql://report-runtime"),
    )
    monkeypatch.setattr(runtime_schema, "_runtime_schema_lock", _Lock)
    monkeypatch.setattr(runtime_schema, "PostgresReportBatchLedger", _BatchLedger)
    monkeypatch.setattr(runtime_schema, "PostgresReportInputSnapshotStore", _SnapshotStore)

    runtime_schema.ensure_runtime_schema()

    assert calls == [
        ("lock_enter", "postgresql://report-runtime"),
        ("batch_init", "postgresql://report-runtime"),
        ("batch_ready", None),
        ("snapshot_init", "postgresql://report-runtime"),
        ("snapshot_ready", None),
        ("lock_exit", "postgresql://report-runtime"),
    ]
