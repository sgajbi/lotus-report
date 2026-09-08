"""Foreign-key enforcement survives the `report_request` rebuild (#369).

The rebuild disables `foreign_keys` so the table can be renamed and recreated,
then restores it. The restoration was written in a `finally` that runs **before
the commit** -- and `PRAGMA foreign_keys` is a no-op inside a transaction, a
rule the rebuild's own comment states three lines above.

Measured against SQLite directly:

    before txn        : 0
    set ON inside txn : 0
    after commit      : 0
    set ON after commit: 1

So every statement on that connection after the migration ran with foreign keys
disabled -- on the connection the ledger keeps using.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.reporting_jobs.ledger import ReportJobLedger


def _pre_350_ledger(path: Path) -> None:
    """A ledger file whose `report_request` still carries the single-key UNIQUE."""
    connection = sqlite3.connect(path)
    connection.executescript(
        """
CREATE TABLE report_request (
    report_request_id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    portfolio_scope_json TEXT NOT NULL,
    requested_output_formats_json TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    reporting_currency TEXT,
    options_json TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    caller_application TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    region TEXT NOT NULL,
    booking_center_code TEXT,
    role TEXT,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
        CREATE UNIQUE INDEX idx_report_request_idempotency
            ON report_request (idempotency_key);
        CREATE TABLE report_job (
            report_job_id TEXT PRIMARY KEY,
            report_request_id TEXT NOT NULL REFERENCES report_request(report_request_id),
            report_type TEXT NOT NULL,
            portfolio_scope_json TEXT NOT NULL
        );
        INSERT INTO report_request (
            report_request_id, report_type, portfolio_scope_json,
            requested_output_formats_json, as_of_date, options_json, trigger_type,
            triggered_by, caller_application, tenant_id, region, idempotency_key,
            request_hash, correlation_id, trace_id, created_at
        ) VALUES (
            'req-1', 'PORTFOLIO_REVIEW', '{}', '[]', '2026-01-01', '{}', 'API',
            'actor', 'lotus-workbench', 'tenant-sg', 'SG', 'idem-1',
            'hash', 'corr', 'trace', '2026-01-01T00:00:00Z'
        );
        INSERT INTO report_job VALUES ('job-1', 'req-1', 'PORTFOLIO_REVIEW', '{}');
        """
    )
    connection.commit()
    connection.close()


def test_foreign_key_enforcement_is_on_for_every_ledger_connection(tmp_path: Path) -> None:
    """Read on a connection the LEDGER opens, which is the only one that matters.

    An earlier version of this test opened its own `sqlite3.connect`, set the
    pragma itself and asserted it was 1 -- which is true of any connection and
    says nothing about the ledger. Falsification proved it worthless: removing
    the restoration entirely left the test passing. `foreign_keys` is
    per-connection, so a test that supplies the setting it is checking cannot
    observe the defect.

    This uses `ledger._connect()`, so a regression that stops enabling the
    pragma for ordinary operations fails here.
    """
    path = tmp_path / "jobs.sqlite3"
    _pre_350_ledger(path)

    ledger = ReportJobLedger(path)

    with ledger._connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1, (
            "every ledger connection must enforce foreign keys, not just the schema build"
        )
        violated = False
        try:
            # Columns named rather than positional: `ensure_schema` creates the
            # real `report_job`, which is wider than the fixture's, and a
            # positional insert would fail on arity before reaching the
            # foreign-key check -- passing this test for the wrong reason.
            connection.execute(
                "INSERT INTO report_job (report_job_id, report_request_id, report_type, "
                "portfolio_scope_json) VALUES ('job-2', 'req-absent', 'PORTFOLIO_REVIEW', '{}')"
            )
        except sqlite3.IntegrityError:
            violated = True
        assert violated, "an orphan job must be refused once enforcement is on"


def test_the_retained_relationship_survives_the_rebuild(tmp_path: Path) -> None:
    """The row and its dependent job are both still there, and still joined.

    The rebuild renames, recreates and copies with foreign keys disabled, so
    nothing in the database is checking the relationship while it happens. This
    asserts the join afterwards rather than the row counts alone: two intact
    tables that no longer reference each other would pass a count check.
    """
    path = tmp_path / "jobs.sqlite3"
    _pre_350_ledger(path)

    ReportJobLedger(path)

    connection = sqlite3.connect(path)
    try:
        joined = connection.execute(
            """
            SELECT j.report_job_id, r.tenant_id
            FROM report_job j
            JOIN report_request r ON r.report_request_id = j.report_request_id
            """
        ).fetchall()
        assert joined == [("job-1", "tenant-sg")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_the_rebuild_leaves_no_half_renamed_table(tmp_path: Path) -> None:
    """A `report_request_pre_350` left behind is a ledger serving from the wrong table."""
    path = tmp_path / "jobs.sqlite3"
    _pre_350_ledger(path)

    ReportJobLedger(path)

    connection = sqlite3.connect(path)
    try:
        leftover = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'report_request_pre_350'"
        ).fetchone()
        assert leftover is None
    finally:
        connection.close()


def test_the_rebuild_leaves_its_own_connection_enforcing(tmp_path: Path) -> None:
    """The restoration, observed on the connection the rebuild actually used.

    `_connect` now enables foreign keys for every connection, which masks this:
    any connection opened afterwards reports 1 whatever the rebuild did. So the
    migration is driven directly here, on a connection this test controls, and
    the pragma is read on that same connection after it returns.

    That is the only place the defect is visible. The restoration used to sit in
    a `finally` that ran before the commit, where `PRAGMA foreign_keys` is a
    no-op -- so the rest of `ensure_schema` ran unenforced on that connection.
    """
    path = tmp_path / "jobs.sqlite3"
    _pre_350_ledger(path)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        ReportJobLedger._migrate_report_request_to_tenant_identity(connection)

        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1, (
            "the rebuild disabled foreign keys and must re-enable them AFTER the commit; "
            "restoring inside the transaction is silently ignored"
        )
        assert connection.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0
    finally:
        connection.close()
