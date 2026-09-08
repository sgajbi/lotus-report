"""Migration 026 rehearsed against a populated table (report#326, #371).

The runner keeps no ledger of applied migrations: it re-executes every file on
every call, and `ensure_runtime_schema()` calls it more than once. So 026 does
not run once at rollout -- it runs at every startup, forever.

It was a `DROP CONSTRAINT` followed by an unconditional `ADD CONSTRAINT`.
Measured through the shipped runner against 400 populated rows, the backing
index came back with a **new relfilenode** on the second run: a full index
rebuild under ACCESS EXCLUSIVE, proportional to the table, at every deployment.
And between the DROP and the ADD the uniqueness guarantee did not exist, so a
concurrent writer could insert the duplicate that then made the ADD fail.

These rehearse the real thing: shipped migrations 000-026 through
`apply_report_schema_migrations` with `migrations_dir`, on a populated table.
Forward-only means no invented historical DDL is needed -- the files that ship
are the ones that run.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from app.reporting_persistence.schema import apply_report_schema_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
CONSTRAINT = "report_request_tenant_idempotency_key"


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for the migration 026 rehearsal")
    return database_url


class _Runner:
    """The `MigrationConnection` shape the runner expects."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> object:
        return self._connection.execute(statement, parameters or None)


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


@pytest.fixture
def database_url() -> str:
    return _database_url()


def _populate(connection: psycopg.Connection, rows: int) -> list[str]:
    """Real rows across several tenants, so identity is actually exercised."""
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


def _relfilenode(connection: psycopg.Connection) -> object:
    row = connection.execute(
        "SELECT relfilenode FROM pg_class WHERE relname = %s", (CONSTRAINT,)
    ).fetchone()
    assert row is not None, "the identity constraint must exist after 026"
    return row[0]


def test_a_repeat_startup_does_not_rebuild_the_identity_index(
    migrated: psycopg.Connection,
) -> None:
    """The measurement that names the defect: a stable relfilenode.

    A rebuilt index gets a new relfilenode, so this distinguishes "the
    constraint is still there" -- which was always true -- from "the constraint
    was not reconstructed", which is the property that costs a lock and a full
    index build at every deployment.
    """
    _populate(migrated, 400)
    before = _relfilenode(migrated)

    apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR)
    migrated.commit()

    assert _relfilenode(migrated) == before, (
        "the identity index was rebuilt by a repeat startup; on a production table that is "
        "an ACCESS EXCLUSIVE lock and a full index build every time the service starts"
    )


def test_a_repeat_startup_preserves_every_identity(migrated: psycopg.Connection) -> None:
    """`report_request_id` is a receipt a consumer already holds.

    Re-deriving one would hand a consumer an identity it has never seen for a
    receipt it already has, which is why 026 does not re-derive it. Asserted
    across a repeat run rather than assumed from the migration text.
    """
    request_ids = _populate(migrated, 200)

    apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR)
    migrated.commit()

    surviving = {
        str(row[0])
        for row in migrated.execute("SELECT report_request_id FROM report_request").fetchall()
    }
    assert surviving == set(request_ids)


def test_the_identity_constraint_still_refuses_a_duplicate_after_a_repeat_run(
    migrated: psycopg.Connection,
) -> None:
    """The guarantee has to survive the idempotence, not be traded for it.

    Making the migration a no-op is only correct if the constraint it would have
    created is still enforcing. A same-tenant duplicate must be refused and a
    different-tenant reuse must be allowed -- the whole point of #350.
    """
    _populate(migrated, 10)

    apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR)
    migrated.commit()

    with pytest.raises(psycopg.errors.UniqueViolation):
        migrated.execute(
            """
            INSERT INTO report_request (
                report_request_id, report_type, portfolio_scope_json,
                requested_output_formats_json, as_of_date, options_json, trigger_type,
                triggered_by, caller_application, tenant_id, region, idempotency_key,
                request_hash, correlation_id, trace_id, created_at
            ) VALUES ('req-dup', 'PORTFOLIO_REVIEW', '{}', '[]', '2026-01-01', '{}', 'API',
                      'actor', 'lotus-workbench', 'tenant-0', 'SG', 'idem-0',
                      'hash', 'corr', 'trace', now())
            """
        )
    migrated.rollback()

    # The same key under a different tenant is not a collision, which is the
    # defect #350 removed: identity is the pair, not the key alone.
    migrated.execute(
        """
        INSERT INTO report_request (
            report_request_id, report_type, portfolio_scope_json,
            requested_output_formats_json, as_of_date, options_json, trigger_type,
            triggered_by, caller_application, tenant_id, region, idempotency_key,
            request_hash, correlation_id, trace_id, created_at
        ) VALUES ('req-other-tenant', 'PORTFOLIO_REVIEW', '{}', '[]', '2026-01-01', '{}', 'API',
                  'actor', 'lotus-workbench', 'tenant-elsewhere', 'SG', 'idem-0',
                  'hash', 'corr', 'trace', now())
        """
    )
    migrated.commit()


def test_a_repeat_startup_takes_no_exclusive_lock_on_the_table(
    migrated: psycopg.Connection, database_url: str
) -> None:
    """Measured with a concurrent instance, because that is where it bites.

    The rebuild took ACCESS EXCLUSIVE on `report_request`, which blocks every
    reader and writer for its duration. A second instance starting -- a rolling
    deployment, exactly when this runs -- would have waited on it.

    Asserted from another connection while the migration's transaction is still
    open: a lock released at commit is invisible afterwards, so checking after
    the fact would pass whatever the migration did.
    """
    _populate(migrated, 200)

    observer = psycopg.connect(database_url, autocommit=True)
    try:
        apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR)

        held = observer.execute(
            """
            SELECT l.mode
            FROM pg_locks l
            JOIN pg_class c ON c.oid = l.relation
            JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE c.relname = 'report_request'
              AND l.pid <> pg_backend_pid()
              AND a.datname = current_database()
            """
        ).fetchall()
        modes = {str(row[0]) for row in held}
        assert "AccessExclusiveLock" not in modes, (
            f"a repeat startup must not lock out readers and writers; held {sorted(modes)}"
        )
        migrated.commit()
    finally:
        observer.close()


def test_an_interrupted_run_leaves_the_constraint_intact_and_a_retry_succeeds(
    migrated: psycopg.Connection, database_url: str
) -> None:
    """Failure and restart recovery, on a populated table.

    A run that dies mid-file rolls back, so the constraint it was about to
    re-create must still be the one that was already there -- not missing, and
    not a rebuilt copy. The retry then completes without touching it.
    """
    request_ids = _populate(migrated, 50)
    before = _relfilenode(migrated)

    interrupted = psycopg.connect(database_url, autocommit=False)
    try:
        apply_report_schema_migrations(_Runner(interrupted), migrations_dir=MIGRATIONS_DIR)
        interrupted.rollback()  # the process dies before committing
    finally:
        interrupted.close()

    assert _relfilenode(migrated) == before, "a rolled-back run must leave the index untouched"

    apply_report_schema_migrations(_Runner(migrated), migrations_dir=MIGRATIONS_DIR)
    migrated.commit()

    assert _relfilenode(migrated) == before
    surviving = {
        str(row[0])
        for row in migrated.execute("SELECT report_request_id FROM report_request").fetchall()
    }
    assert surviving == set(request_ids)
