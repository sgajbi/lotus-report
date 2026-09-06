from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path = [path for path in sys.path if path != str(SRC)]
sys.path.insert(0, str(SRC))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.reporting_persistence.schema import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    LEGACY_STATUS_EVENT_BASELINE,
    ReportSchemaCompatibilityError,
    apply_report_schema_migrations,
    validate_supported_report_schema,
)

LEGACY_FIXTURE = ROOT / "scripts" / "fixtures" / "report_status_event_pre_contract_v0.sql"
LEGACY_EVENT_ID = "event-pre-contract-v0"
EXPECTED_CONTRACT_COLUMNS = {
    "event_schema_version": ("text", "NO"),
    "event_family": ("text", "NO"),
    "event_payload_json": ("jsonb", "NO"),
    "event_idempotency_key": ("text", "YES"),
}
EXPECTED_INDEXES = {
    "idx_report_status_event_family_created",
    "idx_report_status_event_idempotency_key",
}
NULLABILITY_MISMATCHES = {
    "event_schema_version": ("YES", "NO"),
    "event_family": ("YES", "NO"),
    "event_payload_json": ("YES", "NO"),
    "event_idempotency_key": ("NO", "YES"),
}


def run_upgrade_check(database_url: str) -> None:
    schema_name = f"report_upgrade_{uuid4().hex[:12]}"
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        _execute_fixture(connection)

        detected_before = validate_supported_report_schema(connection)
        if detected_before != LEGACY_STATUS_EVENT_BASELINE:
            raise RuntimeError(
                "legacy_upgrade_fixture_mismatch:"
                f"expected={LEGACY_STATUS_EVENT_BASELINE}:actual={detected_before}"
            )

        first_run = apply_report_schema_migrations(connection)
        second_run = apply_report_schema_migrations(connection)

        detected_after = validate_supported_report_schema(connection)
        if detected_after != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "legacy_upgrade_target_mismatch:"
                f"expected={CURRENT_SCHEMA_VERSION}:actual={detected_after}"
            )
        if first_run != second_run:
            raise RuntimeError("legacy_upgrade_migration_order_not_deterministic")

        _verify_contract_columns(connection)
        _verify_legacy_row(connection)
        _verify_intake_ledger_schema(connection)
        _verify_indexes(connection)

        connection.execute("RESET search_path")
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))

        _verify_unsupported_nullability(connection)


def _execute_fixture(connection: psycopg.Connection[dict[str, object]]) -> None:
    fixture = LEGACY_FIXTURE.read_text(encoding="utf-8")
    for statement in fixture.split(";"):
        if statement.strip():
            connection.execute(statement)


def _verify_contract_columns(connection: psycopg.Connection[dict[str, object]]) -> None:
    rows = connection.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'report_status_event'
          AND column_name IN (
              'event_schema_version',
              'event_family',
              'event_payload_json',
              'event_idempotency_key'
          )
        """
    ).fetchall()
    observed = {
        str(row["column_name"]): (str(row["data_type"]), str(row["is_nullable"])) for row in rows
    }
    if observed != EXPECTED_CONTRACT_COLUMNS:
        raise RuntimeError(
            "legacy_upgrade_contract_columns_mismatch:"
            f"expected={EXPECTED_CONTRACT_COLUMNS}:actual={observed}"
        )


def _verify_legacy_row(connection: psycopg.Connection[dict[str, object]]) -> None:
    row = connection.execute(
        """
        SELECT event_schema_version, event_family, event_payload_json, event_idempotency_key,
               message, correlation_id, trace_id
        FROM report_status_event
        WHERE status_event_id = %s
        """,
        (LEGACY_EVENT_ID,),
    ).fetchone()
    if row is None:
        raise RuntimeError("legacy_upgrade_event_missing")
    expected = {
        "event_schema_version": "report-status-event.legacy.v0",
        "event_family": "job_lifecycle",
        "event_payload_json": {"payload_posture": "legacy_message_only"},
        "event_idempotency_key": None,
        "message": "Legacy event retained for executable upgrade proof.",
        "correlation_id": "corr-pre-contract-v0",
        "trace_id": "trace-pre-contract-v0",
    }
    if row != expected:
        raise RuntimeError(f"legacy_upgrade_event_mismatch:expected={expected}:actual={row}")


def _verify_intake_ledger_schema(connection: psycopg.Connection[dict[str, object]]) -> None:
    """Migration 024 must CREATE the intake ledger with its intended types.

    Deliberately not a populated-upgrade proof. An earlier version of this
    function seeded a TEXT-typed `idea_evidence_intake` before migrations and
    then checked a row survived -- but `CREATE TABLE IF NOT EXISTS` no-ops when
    the table exists, so nothing was converted and the check could not fail.
    The seeded state was also fictional: the pre-migration ledger is a SQLite
    FILE, not a PostgreSQL table.

    The populated-upgrade proof belongs with the data migration that moves
    those SQLite rows, and is tracked in report#326.
    """
    rows = connection.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'idea_evidence_intake'
        """
    ).fetchall()
    if not rows:
        raise RuntimeError("intake_ledger_table_missing_after_migration")

    observed = {str(row["column_name"]): str(row["data_type"]) for row in rows}
    expected = {
        "idempotency_key": "text",
        "response_json": "jsonb",
        "caller_context_json": "jsonb",
        "accepted_at_utc": "timestamp with time zone",
        "created_at_utc": "timestamp with time zone",
    }
    for column, data_type in expected.items():
        if observed.get(column) != data_type:
            raise RuntimeError(
                "intake_ledger_column_type_mismatch:"
                f"column={column}:expected={data_type}:actual={observed.get(column)}"
            )


def _verify_indexes(connection: psycopg.Connection[dict[str, object]]) -> None:
    rows = connection.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND indexname IN (
              'idx_report_status_event_family_created',
              'idx_report_status_event_idempotency_key'
          )
        """
    ).fetchall()
    observed = {str(row["indexname"]) for row in rows}
    if observed != EXPECTED_INDEXES:
        raise RuntimeError(
            f"legacy_upgrade_indexes_mismatch:expected={EXPECTED_INDEXES}:actual={observed}"
        )


def _verify_unsupported_nullability(
    connection: psycopg.Connection[dict[str, object]],
) -> None:
    for column, (actual_nullable, expected_nullable) in NULLABILITY_MISMATCHES.items():
        schema_name = f"report_nullability_{uuid4().hex[:12]}"
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
        _execute_fixture(connection)
        apply_report_schema_migrations(connection)

        if actual_nullable == "YES":
            connection.execute(
                sql.SQL("ALTER TABLE report_status_event ALTER COLUMN {} DROP NOT NULL").format(
                    sql.Identifier(column)
                )
            )
        else:
            connection.execute(
                """
                UPDATE report_status_event
                SET event_idempotency_key = 'legacy-event-idempotency-key'
                WHERE event_idempotency_key IS NULL
                """
            )
            connection.execute(
                sql.SQL("ALTER TABLE report_status_event ALTER COLUMN {} SET NOT NULL").format(
                    sql.Identifier(column)
                )
            )

        expected_fragment = (
            f"{column}:nullable={actual_nullable}:expected_nullable={expected_nullable}"
        )
        try:
            apply_report_schema_migrations(connection)
        except ReportSchemaCompatibilityError as exc:
            if expected_fragment not in str(exc):
                raise RuntimeError(
                    "nullability_preflight_diagnostic_mismatch:"
                    f"expected={expected_fragment}:actual={exc}"
                ) from exc
        else:
            raise RuntimeError(f"nullability_preflight_accepted_unsupported:{column}")

        observed = connection.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'report_status_event'
              AND column_name = %s
            """,
            (column,),
        ).fetchone()
        if observed is None or observed["is_nullable"] != actual_nullable:
            raise RuntimeError(
                "nullability_preflight_mutated_unsupported_schema:"
                f"column={column}:expected={actual_nullable}:actual={observed}"
            )

        connection.execute("RESET search_path")
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))


def main() -> int:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        print("REPORT_JOB_LEDGER_DATABASE_URL is required for schema upgrade smoke.")
        return 1
    try:
        run_upgrade_check(database_url)
    except Exception as exc:
        print(f"Report schema upgrade check failed: {exc}")
        return 1
    print(
        "Report schema upgrade check passed "
        f"(source={LEGACY_STATUS_EVENT_BASELINE}, target={CURRENT_SCHEMA_VERSION})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
