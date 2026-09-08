"""The migration runner splits statements without cutting inside quoting (#371).

`schema.split(";")` cut on every semicolon. Two migration files carry comments
explaining how they were written around it -- 025 states it "avoids the
character entirely outside real statement ends", and 026 could not use a `DO`
block for its idempotence guard because the body would have been torn apart.
It used an unconditional DROP-then-ADD instead, which rebuilt the identity
index at every startup.

So the splitter was shaping the migrations. These pin the cases that let a
migration be written for the database instead.
"""

from __future__ import annotations

import pytest

from app.reporting_persistence.schema import split_sql_statements


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ("SELECT 1; SELECT 2;", ["SELECT 1", " SELECT 2"]),
        ("SELECT 1", ["SELECT 1"]),
        ("SELECT 1;", ["SELECT 1"]),
    ],
    ids=["two", "no-trailing-semicolon", "trailing-semicolon"],
)
def test_plain_statements_split_on_the_separator(schema: str, expected: list[str]) -> None:
    assert split_sql_statements(schema) == expected


def test_a_semicolon_inside_a_string_literal_is_not_a_separator() -> None:
    """The most ordinary way the old splitter produced invalid SQL."""
    statements = split_sql_statements("INSERT INTO t VALUES ('a;b'); SELECT 1;")

    assert len(statements) == 2
    assert "'a;b'" in statements[0]


def test_an_escaped_quote_does_not_end_the_literal() -> None:
    """`''` is an escaped quote; treating it as a close reopens the scan mid-string."""
    statements = split_sql_statements("SELECT 'it''s; fine'; SELECT 2;")

    assert len(statements) == 2
    assert "it''s; fine" in statements[0]


@pytest.mark.parametrize(
    "comment",
    ["-- a; comment\n", "/* a; comment */ "],
    ids=["line", "block"],
)
def test_a_semicolon_inside_a_comment_is_not_a_separator(comment: str) -> None:
    """025's warning made concrete: its prose could not contain a semicolon."""
    statements = split_sql_statements(f"{comment}SELECT 1;")

    assert len(statements) == 1


@pytest.mark.parametrize(
    "body",
    [
        "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;",
        "DO $tag$ BEGIN PERFORM 1; END $tag$;",
    ],
    ids=["untagged", "tagged"],
)
def test_a_dollar_quoted_body_survives_intact(body: str) -> None:
    """The case that unlocks 026's idempotence guard.

    A `DO` block is how PostgreSQL expresses "add this constraint only if it is
    absent" without a rebuild. The old splitter cut it into fragments, so the
    migration could not use one.
    """
    statements = split_sql_statements(body)

    assert len(statements) == 1
    assert statements[0].count("BEGIN") == 1
    assert "END" in statements[0]


def test_a_dollar_quoted_block_is_separated_from_what_follows() -> None:
    """Intact is not enough; the statement after it must still be its own."""
    statements = split_sql_statements("DO $$ BEGIN PERFORM 1; END $$; SELECT 2;")

    assert len(statements) == 2
    assert "SELECT 2" in statements[1]


@pytest.mark.parametrize(
    "schema",
    ["", "   ", "-- only a comment", "-- one\n-- two\n", ";", ";;"],
    ids=["empty", "whitespace", "comment", "comments", "semicolon", "semicolons"],
)
def test_nothing_executable_yields_no_statements(schema: str) -> None:
    """A comment-only fragment is not a statement.

    Sending one produces an empty-query error rather than a no-op, so the
    previous `if statement.strip()` filter has to be preserved -- a bare comment
    is truthy after stripping.
    """
    assert split_sql_statements(schema) == []


def test_an_unterminated_dollar_quote_does_not_swallow_the_rest_silently() -> None:
    """A malformed file must still produce something the server can reject.

    Returning nothing would make a broken migration look like an applied one,
    which is the failure mode this whole area is about.
    """
    statements = split_sql_statements("DO $$ BEGIN PERFORM 1;")

    assert statements, "a malformed migration must still reach the server to be refused"


def test_a_backslash_escaped_quote_in_an_escape_string_is_not_the_end() -> None:
    r"""`E'...'` treats backslash as an escape; found in review.

    `E'a\';b'` is ONE literal containing a semicolon. The scanner ended the
    literal at the escaped apostrophe and cut at the semicolon still inside it,
    producing `SELECT E'a\'` and `b'; SELECT 2;` -- two fragments, both invalid
    SQL, so startup would abort on a migration that is perfectly legal.
    """
    statements = split_sql_statements(r"SELECT E'a\';b'; SELECT 2;")

    assert len(statements) == 2
    assert r"a\';b" in statements[0], "the literal must survive intact"
    assert "SELECT 2" in statements[1]


def test_a_lowercase_escape_prefix_is_recognised() -> None:
    """`e'...'` is the same construct; case is not significant in PostgreSQL."""
    assert len(split_sql_statements(r"SELECT e'x\';y';")) == 1


def test_a_plain_literal_does_not_honour_backslash_escapes() -> None:
    r"""The boundary that makes the fix safe rather than merely different.

    With `standard_conforming_strings` on -- the default since PostgreSQL 9.1 --
    a backslash in an ordinary literal is just a backslash. Honouring escapes
    everywhere would make `'a\'` swallow the following statement, trading one
    mis-scan for a worse one.
    """
    statements = split_sql_statements(r"SELECT 'a\'; SELECT 2;")

    assert len(statements) == 2


def test_an_identifier_ending_in_e_is_not_an_escape_string() -> None:
    r"""`value'x'` is an identifier next to a literal, not `E'x'`.

    The literal MUST contain a backslash before a quote, or the test cannot
    tell the two readings apart. An earlier version used `value';'` -- which
    scans identically whether or not backslash escapes are honoured, so it
    exercised the branch without discriminating, and passed with the identifier
    check replaced by `return True`. Found by falsification.
    """
    statements = split_sql_statements(r"SELECT value'a\'; SELECT 2;")

    assert len(statements) == 2, (
        "an identifier ending in `e` must not turn the following literal into an "
        "escape string, or its backslash would swallow the next statement"
    )
