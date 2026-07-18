from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol

from psycopg import Error as PostgresError


class MigrationConnection(Protocol):
    def execute(self, query: str, params: object | None = None) -> Any: ...


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
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'report_status_event'
        """
    ).fetchall()
    observed = {
        str(_row_value(row, "column_name", 0)): str(_row_value(row, "data_type", 1))
        for row in column_rows
    }
    missing_legacy_columns = sorted(LEGACY_STATUS_EVENT_COLUMNS - observed.keys())
    if missing_legacy_columns:
        missing = ",".join(missing_legacy_columns)
        raise ReportSchemaCompatibilityError(
            "report_schema_upgrade_unsupported:"
            f"detected=unrecognized:target={CURRENT_SCHEMA_VERSION}:"
            f"table=report_status_event:missing={missing}"
        )

    incompatible_contract_columns = sorted(
        f"{column}:{observed[column]}"
        for column, expected_type in STATUS_EVENT_CONTRACT_TYPES.items()
        if column in observed and observed[column] != expected_type
    )
    if incompatible_contract_columns:
        incompatible = ",".join(incompatible_contract_columns)
        raise ReportSchemaCompatibilityError(
            "report_schema_upgrade_unsupported:"
            f"detected=unrecognized:target={CURRENT_SCHEMA_VERSION}:"
            f"table=report_status_event:incompatible={incompatible}"
        )

    if STATUS_EVENT_CONTRACT_TYPES.keys() <= observed.keys():
        return CURRENT_SCHEMA_VERSION
    return LEGACY_STATUS_EVENT_BASELINE


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
            for statement in schema.split(";"):
                if statement.strip():
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
