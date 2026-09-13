"""Applied-migration ledger convergence, against real PostgreSQL (report#376).

The runner previously replayed every migration file at every startup, so
historical migrations 002/005/006/007/013/016 re-issued CHECK constraint
drop/add and 025 re-issued `SET NOT NULL` on every boot. The runner now keeps
an applied-migration ledger and executes only unrecorded files. These tests
are the #376 acceptance evidence:

1. a current-schema restart applies nothing and leaves EVERY index untouched,
   not only migration 026's;
2. a fresh installation and a supported legacy upgrade converge to the same
   final schema;
3. a retained pre-ledger install converges through one bridge replay, then
   never replays again;
4. concurrent startups serialize on the transaction-scoped advisory lock —
   observed at the lock in pg_locks, not inferred from timing — and the loser
   applies nothing;
5. an interrupted bridge replay rolls back completely and the retry converges;
6. a pending file that sorts before a recorded one is refused before mutation.

Shipped migrations through `apply_report_schema_migrations` with the real
`migrations/` directory, on populated tables, per the stateful-migration proof
standard: a mocked SQL string cannot prove any of this.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.reporting_persistence.schema import (
    MIGRATION_LEDGER_TABLE,
    ReportSchemaMigrationError,
    apply_report_schema_migrations,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
LEGACY_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "fixtures"
    / "report_status_event_pre_contract_v0.sql"
)
IDENTITY_INDEX = "report_request_tenant_idempotency_key"


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for the migration ledger proof")
    return database_url


@pytest.fixture
def database_url() -> str:
    return _database_url()


class _Runner:
    """The `MigrationConnection` shape the runner expects."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    def execute(self, statement: str) -> object:
        return self._connection.execute(statement)


@pytest.fixture
def migrated(database_url: str) -> psycopg.Connection:
    connection = psycopg.connect(database_url, autocommit=False)
    connection.execute("DROP SCHEMA public CASCADE")
    connection.execute("CREATE SCHEMA public")
    connection.commit()
    apply_report_schema_migrations(_Runner(connection), migrations_dir=MIGRATIONS_DIR)
    connection.commit()
    yield connection
    connection.close()


def _populate(connection: psycopg.Connection, rows: int) -> list[str]:
    request_ids: list[str] = []
    for index in range(rows):
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        request_ids.append(request_id)
        connection.execute(
            """
            INSERT INTO report_request (
                report_request_id, report_type, portfolio_scope_json,
                requested_output_formats_json, as_of_date, options_json, trigger_type,
                triggered_by, caller_application, tenant_id, region, idempotency_key,
                request_hash, correlation_id, trace_id, created_at
            ) VALUES (%s, 'PORTFOLIO_REVIEW', '{}', '[]', '2026-01-01', '{}', 'API',
                      'actor', 'lotus-workbench', %s, 'SG', %s, 'hash', 'corr', 'trace', now())
            """,
            (request_id, f"tenant-{index % 3}", f"idem-{index}"),
        )
    connection.commit()
    return request_ids


def _surviving_request_ids(connection: psycopg.Connection) -> set[str]:
    rows = connection.execute("SELECT report_request_id FROM report_request").fetchall()
    return {str(row[0]) for row in rows}


def _all_index_relfilenodes(connection: psycopg.Connection) -> dict[str, object]:
    """Every index in `public`, so no migration's rebuild can hide behind 026's."""
    rows = connection.execute(
        """
        SELECT c.relname, c.relfilenode
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'i'
        """
    ).fetchall()
    return {str(row[0]): row[1] for row in rows}


def _migration_file_names() -> tuple[str, ...]:
    return tuple(path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql")))


def test_current_schema_restart_applies_nothing_and_preserves_every_index(
    migrated: psycopg.Connection,
) -> None:
    """The #376 invariant: a restart on a current schema executes zero migration DDL.

    Proven by the applied set being empty AND by every index keeping its
    relfilenode — a rebuilt index gets a new one, so this catches a rebuild by
    any migration, not only the 026 identity index the earlier test guarded.
    """
    request_ids = _populate(migrated, 200)
    before = _all_index_relfilenodes(migrated)

    applied = apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR)
    migrated.commit()

    assert applied == (), f"a current-schema restart replayed migrations: {applied}"
    assert _all_index_relfilenodes(migrated) == before, (
        "a repeat startup rebuilt at least one index; on a production table that is an "
        "ACCESS EXCLUSIVE lock and a full index build at every deployment"
    )
    assert _surviving_request_ids(migrated) == set(request_ids)


def _schema_fingerprint(
    connection: psycopg.Connection, schema_name: str
) -> dict[str, set[tuple[object, ...]]]:
    """Columns, constraints and indexes of one schema, normalized for its name."""
    columns = connection.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable,
               COALESCE(column_default, '') AS column_default
        FROM information_schema.columns
        WHERE table_schema = %s
        """,
        (schema_name,),
    ).fetchall()
    constraints = connection.execute(
        """
        SELECT conrelid::regclass::text AS table_name, conname,
               pg_get_constraintdef(oid) AS definition
        FROM pg_constraint
        WHERE connamespace = %s::regnamespace
        """,
        (schema_name,),
    ).fetchall()
    indexes = connection.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s",
        (schema_name,),
    ).fetchall()

    def _normalized(value: object) -> str:
        return str(value).replace(f"{schema_name}.", "")

    return {
        "columns": {tuple(_normalized(field) for field in row) for row in columns},
        "constraints": {tuple(_normalized(field) for field in row) for row in constraints},
        "indexes": {tuple(_normalized(field) for field in row) for row in indexes},
    }


def test_fresh_install_and_legacy_upgrade_converge_to_the_same_schema(
    database_url: str,
) -> None:
    """Acceptance: both supported entry paths reach one final schema.

    A fresh database and the supported `report-status-event-pre-contract-v0`
    retained baseline are migrated in isolated schemas; their column,
    constraint and index inventories must be identical — the definition of
    "same final schema" that presence checks understate.
    """
    fresh_schema = f"report_fresh_{uuid.uuid4().hex[:12]}"
    legacy_schema = f"report_legacy_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        try:
            for schema_name, seed_legacy in ((fresh_schema, False), (legacy_schema, True)):
                connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
                connection.execute(
                    sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name))
                )
                if seed_legacy:
                    for statement in LEGACY_FIXTURE.read_text(encoding="utf-8").split(";"):
                        if statement.strip():
                            connection.execute(statement)
                applied = apply_report_schema_migrations(connection)
                assert applied == _migration_file_names()
                connection.execute("RESET search_path")

            fresh = _schema_fingerprint(connection, fresh_schema)
            legacy = _schema_fingerprint(connection, legacy_schema)
            assert fresh == legacy, (
                "fresh installation and supported legacy upgrade diverged; the migration "
                "set no longer converges to one schema"
            )
        finally:
            connection.execute("RESET search_path")
            for schema_name in (fresh_schema, legacy_schema):
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
                )


def test_pre_ledger_install_converges_once_then_never_replays(
    migrated: psycopg.Connection,
) -> None:
    """A retained install migrated before the ledger existed bridges exactly once.

    Dropping the ledger table reproduces that install: current schema, no
    ledger. The next run replays every file once — the previous startup
    behavior, so 026's internal guard must keep the identity index untouched
    through it — records them, and the run after that applies nothing.
    """
    request_ids = _populate(migrated, 100)
    before = _all_index_relfilenodes(migrated)
    migrated.execute(f"DROP TABLE {MIGRATION_LEDGER_TABLE}")
    migrated.commit()

    bridge = apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR)
    migrated.commit()

    assert bridge == _migration_file_names()
    assert _all_index_relfilenodes(migrated)[IDENTITY_INDEX] == before[IDENTITY_INDEX], (
        "the one-time bridge replay rebuilt the identity index; migration 026's internal "
        "guard must carry the bridge, not only the ledger short-circuit"
    )
    assert _surviving_request_ids(migrated) == set(request_ids)

    assert apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR) == ()
    migrated.commit()


def test_concurrent_startups_serialize_and_the_loser_applies_nothing(
    migrated: psycopg.Connection, database_url: str
) -> None:
    """Two instances starting together — a rolling deployment — must not both migrate.

    Both racers face the full bridge (ledger dropped). The winner holds the
    transaction-scoped advisory lock with its transaction open; the loser is
    OBSERVED waiting ungranted on that lock in pg_locks — a barrier would only
    prove both calls started, not that the database serialized them. After the
    winner commits, the loser proceeds, sees the committed ledger, and applies
    nothing.
    """
    request_ids = _populate(migrated, 100)
    migrated.execute(f"DROP TABLE {MIGRATION_LEDGER_TABLE}")
    migrated.commit()

    winner_applied = apply_report_schema_migrations(
        _Runner(migrated), migrations_dir=MIGRATIONS_DIR
    )
    assert winner_applied == _migration_file_names()
    # The winner's transaction stays open: the xact advisory lock is still held.

    loser_pid: list[int] = []
    loser_applied: list[tuple[str, ...]] = []
    loser_error: list[BaseException] = []

    def _loser() -> None:
        try:
            with psycopg.connect(database_url, autocommit=False) as loser:
                row = loser.execute("SELECT pg_backend_pid()").fetchone()
                assert row is not None
                loser_pid.append(int(row[0]))
                loser_applied.append(
                    apply_report_schema_migrations(_Runner(loser), migrations_dir=MIGRATIONS_DIR)
                )
                loser.commit()
        except BaseException as exc:  # surfaced in the main thread
            loser_error.append(exc)

    thread = threading.Thread(target=_loser)
    thread.start()
    try:
        observer = psycopg.connect(database_url, autocommit=True)
        try:
            deadline = time.monotonic() + 10
            waiting = False
            while time.monotonic() < deadline:
                if loser_error:
                    raise loser_error[0]
                if loser_pid:
                    rows = observer.execute(
                        """
                        SELECT granted FROM pg_locks
                        WHERE pid = %s AND locktype = 'advisory'
                        """,
                        (loser_pid[0],),
                    ).fetchall()
                    if any(row[0] is False for row in rows):
                        waiting = True
                        break
                time.sleep(0.05)
            assert waiting, (
                "the concurrent starter was never observed waiting on the advisory lock; "
                "the serialization this test claims did not happen"
            )
        finally:
            observer.close()

        migrated.commit()  # releases the xact lock; the loser may now proceed
        thread.join(timeout=30)
        assert not thread.is_alive(), "the concurrent starter never completed"
        if loser_error:
            raise loser_error[0]
    finally:
        if thread.is_alive():  # pragma: no cover - releases a wedged racer
            migrated.rollback()
            thread.join(timeout=30)

    assert loser_applied == [()], (
        f"the losing concurrent starter re-applied migrations: {loser_applied}"
    )
    assert _surviving_request_ids(migrated) == set(request_ids)
    ledger_rows = migrated.execute(
        f"SELECT migration_name, count(*) FROM {MIGRATION_LEDGER_TABLE} GROUP BY migration_name"
    ).fetchall()
    assert {str(row[0]) for row in ledger_rows} == set(_migration_file_names())
    assert all(int(row[1]) == 1 for row in ledger_rows)


def test_interrupted_bridge_replay_rolls_back_and_the_retry_converges(
    migrated: psycopg.Connection, database_url: str
) -> None:
    """A bridge replay that dies mid-run leaves the pre-ledger state intact.

    The whole run — ledger bootstrap, every file, every ledger row — is one
    transaction, so rollback restores exactly the retained install: no ledger,
    unchanged identity index, unchanged receipts. The retry then converges.
    """
    request_ids = _populate(migrated, 50)
    before = _all_index_relfilenodes(migrated)
    migrated.execute(f"DROP TABLE {MIGRATION_LEDGER_TABLE}")
    migrated.commit()

    interrupted = psycopg.connect(database_url, autocommit=False)
    try:
        apply_report_schema_migrations(_Runner(interrupted), migrations_dir=MIGRATIONS_DIR)
        interrupted.rollback()  # the process dies before committing
    finally:
        interrupted.close()

    ledger_exists = migrated.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'report_schema_migration'
        )
        """
    ).fetchone()
    assert ledger_exists is not None and ledger_exists[0] is False, (
        "a rolled-back bridge replay must not leave a partially recorded ledger"
    )
    assert _all_index_relfilenodes(migrated)[IDENTITY_INDEX] == before[IDENTITY_INDEX]
    assert _surviving_request_ids(migrated) == set(request_ids)

    retry = apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR)
    migrated.commit()
    assert retry == _migration_file_names()
    assert apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR) == ()
    migrated.commit()
    assert _surviving_request_ids(migrated) == set(request_ids)


def test_out_of_order_pending_is_refused_before_mutation(database_url: str, tmp_path: Path) -> None:
    """A file interleaved below the recorded history is refused, and applies nothing."""
    (tmp_path / "001_first.sql").write_text("CREATE TABLE t_first(id TEXT);", encoding="utf-8")
    (tmp_path / "003_third.sql").write_text("CREATE TABLE t_third(id TEXT);", encoding="utf-8")
    schema_name = f"report_order_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(database_url) as connection:
        try:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
            applied = apply_report_schema_migrations(connection, migrations_dir=tmp_path)
            assert applied == ("001_first.sql", "003_third.sql")
            connection.commit()

            (tmp_path / "002_between.sql").write_text(
                "CREATE TABLE t_between(id TEXT);", encoding="utf-8"
            )
            with pytest.raises(
                ReportSchemaMigrationError,
                match="report_schema_migration_out_of_order:pending=002_between.sql",
            ):
                apply_report_schema_migrations(connection, migrations_dir=tmp_path)
            connection.rollback()

            missing = connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = 't_between'
                )
                """,
                (schema_name,),
            ).fetchone()
            assert missing is not None and missing[0] is False
        finally:
            connection.execute("RESET search_path")
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
            )
            connection.commit()
