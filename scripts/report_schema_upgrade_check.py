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


#: The replay identity carried across the upgrade. Named separately because it
#: is the value whose survival the check exists to prove.
LEGACY_INTAKE_IDEMPOTENCY_KEY = "intake-idempotency-pre-migration-1"


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
        _verify_intake_ledger_row(connection)
        _verify_indexes(connection)

        connection.execute("RESET search_path")
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))

        _verify_unsupported_nullability(connection)


def _execute_fixture(connection: psycopg.Connection[dict[str, object]]) -> None:
    fixture = LEGACY_FIXTURE.read_text(encoding="utf-8")
    for statement in fixture.split(";"):
        if statement.strip():
            connection.execute(statement)
    _seed_pre_migration_intake_ledger(connection)


def _seed_pre_migration_intake_ledger(
    connection: psycopg.Connection[dict[str, object]],
) -> None:
    """Create the intake ledger in its PRE-migration shape and populate it.

    The pre-#326 ledger was SQLite: TEXT timestamps and TEXT json, because
    SQLite has neither type. Seeding it that way is the point -- a check that
    seeds rows already in the target shape proves the migration is harmless to
    data it never has to convert.
    """
    connection.execute(
        """
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
    )
    connection.execute(
        """
        INSERT INTO idea_evidence_intake (
            idempotency_key, intake_id, payload_fingerprint, response_json,
            caller_context_json, report_evidence_pack_id, conversion_intent_id,
            candidate_id, evidence_packet_id, evidence_content_fingerprint,
            producer, supportability_status, accepted_at_utc, created_at_utc,
            correlation_id, trace_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            LEGACY_INTAKE_IDEMPOTENCY_KEY,
            "intake-pre-migration-1",
            "fingerprint-pre-migration-1",
            '{"posture": "pre_migration"}',
            '{"tenant_id": "tenant-pre-migration"}',
            "pack-pre-migration-1",
            "intent-pre-migration-1",
            "candidate-pre-migration-1",
            "packet-pre-migration-1",
            "content-fingerprint-pre-migration-1",
            "lotus-idea",
            "supported",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "corr-pre-migration",
            "trace-pre-migration",
        ),
    )


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


def _verify_intake_ledger_row(connection: psycopg.Connection[dict[str, object]]) -> None:
    """The populated intake row must survive with its replay identity intact.

    Compared field-by-field rather than by row count: a surviving count with a
    rewritten `idempotency_key` is still a broken idempotency guarantee, because
    that key is what proves a later intake is a replay.
    """
    row = connection.execute(
        """
        SELECT idempotency_key, intake_id, payload_fingerprint, report_evidence_pack_id,
               evidence_packet_id, producer, supportability_status, correlation_id, trace_id
        FROM idea_evidence_intake
        WHERE idempotency_key = %s
        """,
        (LEGACY_INTAKE_IDEMPOTENCY_KEY,),
    ).fetchone()
    if row is None:
        raise RuntimeError("intake_ledger_upgrade_row_missing")
    expected = {
        "idempotency_key": LEGACY_INTAKE_IDEMPOTENCY_KEY,
        "intake_id": "intake-pre-migration-1",
        "payload_fingerprint": "fingerprint-pre-migration-1",
        "report_evidence_pack_id": "pack-pre-migration-1",
        "evidence_packet_id": "packet-pre-migration-1",
        "producer": "lotus-idea",
        "supportability_status": "supported",
        "correlation_id": "corr-pre-migration",
        "trace_id": "trace-pre-migration",
    }
    if row != expected:
        raise RuntimeError(f"intake_ledger_upgrade_row_mismatch:expected={expected}:actual={row}")

    total = connection.execute("SELECT count(*) AS n FROM idea_evidence_intake").fetchone()
    if total is None or total["n"] != 1:
        raise RuntimeError(f"intake_ledger_upgrade_row_count_mismatch:actual={total}")


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
