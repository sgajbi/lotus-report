"""Retained intake rows are attributed truthfully or not at all (#369).

The tenant-scoped upgrade reads each retained row's stored caller context and
promotes the tenant into the primary key. Two ways that went wrong, both
measured against the shipped ledger:

* `TRIM(json_extract(context, '$.tenant_id'))` renders **any** JSON type as
  text, so a stored `123`, `true`, `{}`, `[]` or `1.5` became the tenant
  `'123'`, `'1'`, `'{}'`, `'[]'` or `'1.5'`. Those rows were accepted and
  written. An invented owner is worse than an absent one: `true` becoming `'1'`
  can collide with a real tenant named `1`, and nothing downstream can tell the
  difference afterwards.
* A single row whose context was not valid JSON aborted the whole upgrade with
  SQLite's bare `malformed JSON`, naming no row (#360) -- unactionable against a
  ledger of thousands.

Every case here asserts the **stored** outcome, not just the exception: a
refusal that still wrote something is not a refusal.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.idea_evidence_intake.service import (
    IdeaEvidenceIntakeLedger,
    IdeaEvidenceIntakeMigrationError,
)

PRE_344_DDL = """
CREATE TABLE idea_evidence_intake (
    idempotency_key TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    response_json TEXT NOT NULL,
    caller_context_json TEXT NOT NULL,
    report_evidence_pack_id TEXT NOT NULL,
    conversion_intent_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    evidence_packet_id TEXT NOT NULL,
    evidence_content_fingerprint TEXT NOT NULL,
    producer TEXT NOT NULL,
    supportability_status TEXT NOT NULL,
    accepted_at_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    correlation_id TEXT,
    trace_id TEXT
)
"""


def _pre_344_ledger(path: Path, rows: list[tuple[str, str]]) -> None:
    """A ledger file on the pre-#344 schema holding `(key, caller_context_json)`."""
    connection = sqlite3.connect(path)
    connection.execute(PRE_344_DDL)
    for key, context_json in rows:
        connection.execute(
            "INSERT INTO idea_evidence_intake VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                f"intake-{key}",
                "fingerprint",
                json.dumps({"accepted": True}),
                context_json,
                "pack",
                "intent",
                "candidate",
                "packet",
                "content-fingerprint",
                "lotus-idea",
                "supported",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                None,
                None,
            ),
        )
    connection.commit()
    connection.close()


def _stored_tenants(path: Path) -> list[tuple[str, str]]:
    connection = sqlite3.connect(path)
    try:
        return [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT idempotency_key, tenant_id FROM idea_evidence_intake"
            )
        ]
    finally:
        connection.close()


def _original_table_intact(path: Path, expected_rows: int) -> None:
    connection = sqlite3.connect(path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(idea_evidence_intake)")}
        assert "tenant_id" not in columns, "a refused upgrade must leave the old schema in place"
        count = connection.execute("SELECT count(*) FROM idea_evidence_intake").fetchone()[0]
        assert count == expected_rows, "a refused upgrade must not drop a retained receipt"
        renamed = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'idea_evidence_intake_pre_344'"
        ).fetchone()
        assert renamed is None, "a refused upgrade must leave no half-renamed table behind"
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("context_json", "json_kind"),
    [
        ('{"tenant_id": 123}', "integer"),
        ('{"tenant_id": 1.5}', "real"),
        ('{"tenant_id": true}', "true"),
        ('{"tenant_id": {}}', "object"),
        ('{"tenant_id": []}', "array"),
    ],
    ids=["integer", "float", "boolean", "object", "array"],
)
def test_a_tenant_that_is_not_a_string_is_refused_and_named(
    tmp_path: Path, context_json: str, json_kind: str
) -> None:
    """Measured before the fix: each of these was ACCEPTED and written.

    `123` became `'123'`, `true` became `'1'`, `{}` became `'{}'`. The refusal
    names the row and the JSON type it found, because "not a string" is not
    enough for an operator to decide what the row's real owner was.
    """
    path = tmp_path / "intake.sqlite3"
    _pre_344_ledger(path, [("key-1", context_json)])

    with pytest.raises(IdeaEvidenceIntakeMigrationError) as refusal:
        IdeaEvidenceIntakeLedger(path)

    message = str(refusal.value)
    assert "not a JSON string" in message
    assert "'key-1'" in message, "the operator cannot act on a count alone"
    assert json_kind in message, "the stored type is what tells them what happened"
    _original_table_intact(path, expected_rows=1)


def test_malformed_json_names_the_row_instead_of_aborting(tmp_path: Path) -> None:
    """#360: this aborted with SQLite's bare `malformed JSON`, naming nothing.

    `json_valid` is evaluated first and separately, so malformed rows leave the
    set before any `json_*` accessor touches them -- which is what makes the
    classification possible at all.
    """
    path = tmp_path / "intake.sqlite3"
    _pre_344_ledger(path, [("key-truncated", '{"tenant_id": "tenant-sg"'), ("key-empty", "")])

    with pytest.raises(IdeaEvidenceIntakeMigrationError) as refusal:
        IdeaEvidenceIntakeLedger(path)

    message = str(refusal.value)
    assert "not valid JSON" in message
    assert "2 row(s)" in message
    assert "'key-empty'" in message
    _original_table_intact(path, expected_rows=2)


@pytest.mark.parametrize(
    "context_json",
    ['{"caller_service": "lotus-idea"}', '{"tenant_id": null}', '{"tenant_id": "   "}'],
    ids=["absent", "null", "blank"],
)
def test_an_absent_or_blank_tenant_is_still_refused(tmp_path: Path, context_json: str) -> None:
    """The case #344 already refused, held so the rewrite did not lose it."""
    path = tmp_path / "intake.sqlite3"
    _pre_344_ledger(path, [("key-1", context_json)])

    with pytest.raises(IdeaEvidenceIntakeMigrationError) as refusal:
        IdeaEvidenceIntakeLedger(path)

    assert "no tenant" in str(refusal.value)
    _original_table_intact(path, expected_rows=1)


def test_a_mixture_of_bad_rows_is_refused_once_with_every_class_named(tmp_path: Path) -> None:
    """One run, one refusal, all three classes -- not one class per re-run.

    An operator fixing a production ledger needs the whole picture before they
    start. Reporting the first class that happens to be found sends them round
    the loop once per class, and each loop is another run against a file the
    runbook says to preserve.
    """
    path = tmp_path / "intake.sqlite3"
    _pre_344_ledger(
        path,
        [
            ("key-good", '{"tenant_id": "tenant-sg"}'),
            ("key-broken", '{"tenant_id": '),
            ("key-number", '{"tenant_id": 42}'),
            ("key-absent", '{"caller_service": "lotus-idea"}'),
        ],
    )

    with pytest.raises(IdeaEvidenceIntakeMigrationError) as refusal:
        IdeaEvidenceIntakeLedger(path)

    message = str(refusal.value)
    assert "not valid JSON" in message
    assert "not a JSON string" in message
    assert "no tenant" in message
    assert "'key-broken'" in message
    assert "'key-number'" in message
    assert "'key-absent'" in message
    assert "'key-good'" not in message, "a valid row must not be reported as a problem"
    _original_table_intact(path, expected_rows=4)


def test_a_ledger_whose_rows_all_name_a_real_tenant_is_carried_forward(tmp_path: Path) -> None:
    """The accepting path, so the refusals above are not vacuous.

    A guard that refuses everything is indistinguishable from one that works
    until something legitimate arrives.
    """
    path = tmp_path / "intake.sqlite3"
    _pre_344_ledger(
        path,
        [
            ("key-1", '{"tenant_id": "tenant-sg"}'),
            ("key-2", '{"tenant_id": "  tenant-hk  "}'),
        ],
    )

    IdeaEvidenceIntakeLedger(path)

    assert sorted(_stored_tenants(path)) == [
        ("key-1", "tenant-sg"),
        ("key-2", "tenant-hk"),
    ], "surrounding whitespace is trimmed; the tenant itself is carried verbatim"


def test_the_upgrade_is_idempotent_on_an_already_migrated_file(tmp_path: Path) -> None:
    """Re-opening a migrated ledger must not re-run the rebuild."""
    path = tmp_path / "intake.sqlite3"
    _pre_344_ledger(path, [("key-1", '{"tenant_id": "tenant-sg"}')])

    IdeaEvidenceIntakeLedger(path)
    IdeaEvidenceIntakeLedger(path)

    assert _stored_tenants(path) == [("key-1", "tenant-sg")]
