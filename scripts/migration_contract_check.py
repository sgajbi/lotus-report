from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path = [path for path in sys.path if path != str(SRC)]
sys.path.insert(0, str(SRC))

import psycopg  # noqa: E402

from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger  # noqa: E402
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore  # noqa: E402

REQUIRED_DOC = Path("docs/standards/migration-contract.md")
REQUIRED_PHRASES = (
    "report job ledger schema",
    "forward-fix",
    "forward-only schema",
    "report_request",
    "report_job",
    "report_status_event",
    "report_input_snapshot",
    "report_upstream_call",
    "archive_request_id",
    "archive_document_id",
    "archive_completed_at",
)


def run_ledger_schema_checks() -> int:
    if not REQUIRED_DOC.exists():
        print(f"Missing required migration contract document: {REQUIRED_DOC}")
        return 1

    content = REQUIRED_DOC.read_text(encoding="utf-8").lower()
    missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in content]
    if missing:
        print("Migration contract document is missing required phrases:")
        for phrase in missing:
            print(f"- {phrase}")
        return 1

    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        print("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL migration smoke.")
        return 1

    ledger = PostgresReportJobLedger(database_url)
    ledger.check_ready()
    snapshot_store = PostgresReportInputSnapshotStore(database_url)
    snapshot_store.check_ready()

    with psycopg.connect(database_url) as connection:
        table_rows = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('report_request', 'report_job', 'report_status_event')
            """
        ).fetchall()
        tables = {row[0] for row in table_rows}
        missing_tables = {"report_request", "report_job", "report_status_event"} - tables
        if missing_tables:
            print(f"Ledger schema smoke failed: missing tables {sorted(missing_tables)}")
            return 1

        snapshot_table_rows = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('report_input_snapshot', 'report_upstream_call')
            """
        ).fetchall()
        snapshot_tables = {row[0] for row in snapshot_table_rows}
        missing_snapshot_tables = {
            "report_input_snapshot",
            "report_upstream_call",
        } - snapshot_tables
        if missing_snapshot_tables:
            print(f"Ledger schema smoke failed: missing tables {sorted(missing_snapshot_tables)}")
            return 1

        index_rows = connection.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname IN (
                  'idx_report_request_created',
                  'idx_report_request_tenant_region_created',
                  'idx_report_request_as_of_date',
                  'idx_report_request_scope_created',
                  'idx_report_job_status_updated',
                  'idx_report_job_created',
                  'idx_report_job_completed',
                  'idx_report_job_request',
                  'idx_report_job_archive_document',
                  'idx_report_status_event_job_created',
                  'idx_report_input_snapshot_created',
                  'idx_report_input_snapshot_supportability',
                  'idx_report_input_snapshot_report_type_created',
                  'idx_report_upstream_call_snapshot',
                  'idx_report_upstream_call_service_endpoint',
                  'idx_report_upstream_call_supportability',
                  'idx_report_upstream_call_created'
              )
            """
        ).fetchall()
        indexes = {row[0] for row in index_rows}
        missing_indexes = {
            "idx_report_request_created",
            "idx_report_request_tenant_region_created",
            "idx_report_request_as_of_date",
            "idx_report_request_scope_created",
            "idx_report_job_status_updated",
            "idx_report_job_created",
            "idx_report_job_completed",
            "idx_report_job_request",
            "idx_report_job_archive_document",
            "idx_report_status_event_job_created",
            "idx_report_input_snapshot_created",
            "idx_report_input_snapshot_supportability",
            "idx_report_input_snapshot_report_type_created",
            "idx_report_upstream_call_snapshot",
            "idx_report_upstream_call_service_endpoint",
            "idx_report_upstream_call_supportability",
            "idx_report_upstream_call_created",
        } - indexes
        if missing_indexes:
            print(f"Ledger schema smoke failed: missing indexes {sorted(missing_indexes)}")
            return 1

        unique_rows = connection.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'report_request'::regclass
              AND contype = 'u'
              AND conkey = ARRAY[
                  (
                      SELECT attnum
                      FROM pg_attribute
                      WHERE attrelid = 'report_request'::regclass
                        AND attname = 'idempotency_key'
                  )
              ]::smallint[]
            """
        ).fetchall()
        if not unique_rows:
            print("Ledger schema smoke failed: idempotency_key uniqueness is missing.")
            return 1

        failure_category_rows = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'report_job'::regclass
              AND conname = 'report_job_failure_category_check'
            """
        ).fetchall()
        if not failure_category_rows:
            print("Ledger schema smoke failed: failure category check constraint is missing.")
            return 1
        failure_category_constraint = str(failure_category_rows[0][0])
        for category in (
            "entitlement_failed",
            "validation_failed",
            "upstream_data_failed",
            "data_incomplete",
            "timeout",
            "cancelled",
            "operator_intervention_required",
            "archive_validation_failed",
            "archive_conflict",
            "archive_storage_failed",
            "archive_execution_failed",
        ):
            if category not in failure_category_constraint:
                print(
                    "Ledger schema smoke failed: failure category check constraint "
                    f"is missing {category}."
                )
                return 1

        status_rows = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'report_job'::regclass
              AND conname = 'report_job_status_check'
            """
        ).fetchall()
        if not status_rows:
            print("Ledger schema smoke failed: report job status check constraint is missing.")
            return 1
        status_constraint = str(status_rows[0][0])
        for status in ("archiving", "archived"):
            if status not in status_constraint:
                print(
                    "Ledger schema smoke failed: report job status check constraint "
                    f"is missing {status}."
                )
                return 1

        archive_column_rows = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'report_job'
              AND column_name IN (
                  'archive_request_id',
                  'archive_document_id',
                  'archive_completed_at'
              )
            """
        ).fetchall()
        archive_columns = {row[0] for row in archive_column_rows}
        missing_archive_columns = {
            "archive_request_id",
            "archive_document_id",
            "archive_completed_at",
        } - archive_columns
        if missing_archive_columns:
            print(
                "Ledger schema smoke failed: missing archive columns "
                f"{sorted(missing_archive_columns)}"
            )
            return 1

        snapshot_constraint_rows = connection.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'report_input_snapshot'::regclass
              AND contype = 'u'
            """
        ).fetchall()
        if not any("report_job_id" in str(row[0]) for row in snapshot_constraint_rows):
            print(
                "Ledger schema smoke failed: "
                "report_input_snapshot.report_job_id uniqueness is missing."
            )
            return 1

        upstream_failure_category_rows = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'report_upstream_call'::regclass
              AND contype = 'c'
              AND conname LIKE '%failure_category%'
            """
        ).fetchall()
        if not upstream_failure_category_rows:
            print(
                "Ledger schema smoke failed: "
                "report_upstream_call failure category check is missing."
            )
            return 1
        upstream_failure_category_constraint = str(upstream_failure_category_rows[0][0])
        for category in (
            "none",
            "partial_data",
            "unsupported_input",
            "upstream_unavailable",
            "upstream_error",
            "timeout",
            "redacted",
        ):
            if category not in upstream_failure_category_constraint:
                print(
                    "Ledger schema smoke failed: upstream failure category check constraint "
                    f"is missing {category}."
                )
                return 1

    print("Migration contract check passed (PostgreSQL report job ledger schema mode).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate migration contract requirements.")
    parser.add_argument("--mode", choices=["ledger-schema", "no-schema"], default="ledger-schema")
    args = parser.parse_args()

    if args.mode in {"ledger-schema", "no-schema"}:
        return run_ledger_schema_checks()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
