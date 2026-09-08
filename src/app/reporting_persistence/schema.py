from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol

from psycopg import Error as PostgresError


class MigrationConnection(Protocol):
    """The one capability the migration runner needs from a connection.

    Deliberately narrower than it was. The parameter list previously carried
    ``params: object | None``, which no real driver satisfies: psycopg accepts
    a sequence or mapping, not an arbitrary object, and protocol parameters are
    contravariant. The runner never passes parameters -- every statement it
    executes is a literal string -- so declaring the parameter made a real
    ``Connection`` structurally incompatible with a protocol it does in fact
    implement, in exchange for describing an argument nothing supplies.
    """

    def execute(self, query: Any) -> Any: ...


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"
CURRENT_SCHEMA_VERSION = "report-ledger-v1"
LEGACY_STATUS_EVENT_BASELINE = "report-status-event-pre-contract-v0"
LEGACY_STATUS_EVENT_COLUMNS = frozenset(
    {
        "status_event_id",
        "report_job_id",
        "from_status",
        "to_status",
        "event_type",
        "message",
        "actor",
        "created_at",
        "correlation_id",
        "trace_id",
    }
)
STATUS_EVENT_CONTRACT_TYPES = {
    "event_schema_version": "text",
    "event_family": "text",
    "event_payload_json": "jsonb",
    "event_idempotency_key": "text",
}
STATUS_EVENT_CONTRACT_NULLABILITY = {
    "event_schema_version": "NO",
    "event_family": "NO",
    "event_payload_json": "NO",
    "event_idempotency_key": "YES",
}


class ReportSchemaError(RuntimeError):
    """Base class for product-safe Report schema startup failures."""


class ReportSchemaCompatibilityError(ReportSchemaError):
    """Raised before mutation when an existing schema is not a supported baseline."""


class ReportSchemaMigrationError(ReportSchemaError):
    """Raised when an ordered migration cannot be applied transactionally."""


def validate_supported_report_schema(connection: MigrationConnection) -> str:
    """Classify the existing status-event schema before applying migrations."""

    table_row = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'report_status_event'
        ) AS table_exists
        """
    ).fetchone()
    if not bool(_row_value(table_row, "table_exists", 0)):
        return "empty"

    column_rows = connection.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'report_status_event'
        """
    ).fetchall()
    observed_types = {
        str(_row_value(row, "column_name", 0)): str(_row_value(row, "data_type", 1))
        for row in column_rows
    }
    observed_nullability = {
        str(_row_value(row, "column_name", 0)): str(_row_value(row, "is_nullable", 2))
        for row in column_rows
    }
    missing_legacy_columns = sorted(LEGACY_STATUS_EVENT_COLUMNS - observed_types.keys())
    if missing_legacy_columns:
        missing = ",".join(missing_legacy_columns)
        raise ReportSchemaCompatibilityError(
            "report_schema_upgrade_unsupported:"
            f"detected=unrecognized:target={CURRENT_SCHEMA_VERSION}:"
            f"table=report_status_event:missing={missing}"
        )

    incompatible_contract_columns = sorted(
        f"{column}:type={observed_types[column]}:expected_type={expected_type}"
        for column, expected_type in STATUS_EVENT_CONTRACT_TYPES.items()
        if column in observed_types and observed_types[column] != expected_type
    )
    incompatible_contract_columns.extend(
        sorted(
            f"{column}:nullable={observed_nullability[column]}:"
            f"expected_nullable={expected_nullable}"
            for column, expected_nullable in STATUS_EVENT_CONTRACT_NULLABILITY.items()
            if column in observed_nullability and observed_nullability[column] != expected_nullable
        )
    )
    if incompatible_contract_columns:
        incompatible = ",".join(incompatible_contract_columns)
        raise ReportSchemaCompatibilityError(
            "report_schema_upgrade_unsupported:"
            f"detected=unrecognized:target={CURRENT_SCHEMA_VERSION}:"
            f"table=report_status_event:incompatible={incompatible}"
        )

    if STATUS_EVENT_CONTRACT_TYPES.keys() <= observed_types.keys():
        return CURRENT_SCHEMA_VERSION
    return LEGACY_STATUS_EVENT_BASELINE


def _is_escape_string(schema: str, index: int) -> bool:
    """Whether the literal opening at `index` is a PostgreSQL `E'...'` string.

    The `E` must be immediately before the quote and must not be the tail of a
    longer identifier, or a column named `value` would make `value'x'` look like
    an escape string.

    This matters only for `E` strings. With `standard_conforming_strings` on --
    the default since PostgreSQL 9.1 -- a backslash in an ordinary literal is
    just a backslash, so honouring escapes everywhere would mis-scan
    `'a\\'` and swallow the rest of the file.
    """
    if index == 0 or schema[index - 1] not in "Ee":
        return False
    preceding = schema[index - 2] if index >= 2 else ""
    return not (preceding.isalnum() or preceding == "_")


def _scan_single_quoted(schema: str, index: int) -> int | None:
    """End index of a `'...'` literal starting at `index`, or None.

    `''` inside a literal is an escaped quote, not the end of one. Inside an
    `E'...'` escape string a backslash also escapes the next character, so
    `E'a\\';b'` is one literal containing a semicolon -- found in review; the
    scanner previously ended the literal at the escaped apostrophe and cut the
    statement at a semicolon that was still inside it, producing two invalid
    fragments rather than one statement.
    """
    if schema[index] != "'":
        return None
    backslash_escapes = _is_escape_string(schema, index)
    cursor = index + 1
    length = len(schema)
    while cursor < length:
        if backslash_escapes and schema[cursor] == "\\":
            cursor += 2
        elif schema[cursor] != "'":
            cursor += 1
        elif cursor + 1 < length and schema[cursor + 1] == "'":
            cursor += 2
        else:
            return cursor + 1
    return length


def _scan_dollar_quoted(schema: str, index: int) -> int | None:
    """End index of a `$$...$$` or `$tag$...$tag$` body, or None.

    This is what a `DO` block needs and what the previous splitter destroyed.
    """
    if schema[index] != "$":
        return None
    tag_end = schema.find("$", index + 1)
    if tag_end == -1:
        return None
    tag_body = schema[index + 1 : tag_end]
    if tag_body and not tag_body.replace("_", "").isalnum():
        return None
    tag = schema[index : tag_end + 1]
    close = schema.find(tag, tag_end + 1)
    return None if close == -1 else close + len(tag)


def _scan_comment(schema: str, index: int) -> int | None:
    """End index of a `--` line comment or a `/* */` block comment, or None."""
    if schema.startswith("--", index):
        end = schema.find("\n", index)
        return len(schema) if end == -1 else end
    if schema.startswith("/*", index):
        end = schema.find("*/", index + 2)
        return len(schema) if end == -1 else end + 2
    return None


#: Tried in order at each character; the first that claims a span wins.
_SPAN_SCANNERS = (_scan_single_quoted, _scan_dollar_quoted, _scan_comment)


def split_sql_statements(schema: str) -> list[str]:
    """Split a migration file into statements, respecting SQL quoting.

    `schema.split(";")` cut on every semicolon, including ones inside string
    literals, comments and dollar-quoted bodies. That is why migration 025
    carries a paragraph explaining that it "avoids the character entirely
    outside real statement ends", and why 026 could not use a `DO` block to
    make its constraint idempotent -- the runner would have torn the body
    apart, so it used an unconditional DROP-then-ADD that rebuilt the index at
    every startup.

    The splitter was shaping the migrations rather than the migrations being
    written for the database.
    """
    statements: list[str] = []
    current: list[str] = []
    index = 0
    length = len(schema)
    while index < length:
        span_end = next(
            (end for scan in _SPAN_SCANNERS if (end := scan(schema, index)) is not None),
            None,
        )
        if span_end is not None:
            current.append(schema[index:span_end])
            index = span_end
            continue
        if schema[index] == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(schema[index])
        index += 1

    statements.append("".join(current))
    return [statement for statement in statements if _is_executable(statement)]


def _is_executable(statement: str) -> bool:
    """Whether a split fragment holds anything for the server to run.

    A trailing fragment of comments and whitespace is not a statement; sending
    it produces an empty-query error rather than a no-op.
    """
    stripped = statement.strip()
    if not stripped:
        return False
    for line in stripped.splitlines():
        text = line.strip()
        if text and not text.startswith("--"):
            return True
    return False


def apply_report_schema_migrations(
    connection: MigrationConnection,
    *,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> tuple[str, ...]:
    """Apply the ordered, forward-only Report schema using the production path."""

    validate_supported_report_schema(connection)
    applied: list[str] = []
    for migration_path in sorted(migrations_dir.glob("*.sql")):
        schema = migration_path.read_text(encoding="utf-8")
        try:
            for statement in split_sql_statements(schema):
                connection.execute(statement)
        except PostgresError as exc:
            sqlstate = exc.sqlstate or "unknown"
            raise ReportSchemaMigrationError(
                "report_schema_migration_failed:"
                f"migration={migration_path.name}:sqlstate={sqlstate}:"
                f"target={CURRENT_SCHEMA_VERSION}"
            ) from exc
        applied.append(migration_path.name)
    return tuple(applied)


def _row_value(row: object, name: str, index: int) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]  # type: ignore[index]
