from __future__ import annotations

from pathlib import Path
from typing import get_args

from app.reporting_jobs.models import ReportFailureCategory

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"
LEGACY_UPGRADE_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "fixtures"
    / "report_status_event_pre_contract_v0.sql"
)
REPORT_STATUS_EVENT_CONTRACT_COLUMNS = {
    "event_schema_version",
    "event_family",
    "event_payload_json",
    "event_idempotency_key",
}


def test_report_job_failure_category_migrations_cover_model_vocabulary() -> None:
    categories = set(get_args(ReportFailureCategory))
    migration_paths = [
        MIGRATIONS_DIR / "001_report_job_ledger.sql",
        MIGRATIONS_DIR / "002_report_job_failure_category_and_operational_indexes.sql",
        MIGRATIONS_DIR / "005_report_job_render_lifecycle.sql",
        MIGRATIONS_DIR / "006_report_job_archive_handoff.sql",
        MIGRATIONS_DIR / "008_report_rerender_attempt.sql",
        MIGRATIONS_DIR / "013_report_failure_category_render_artifact_unrecoverable.sql",
    ]

    for path in migration_paths:
        sql = path.read_text(encoding="utf-8")
        missing = sorted(category for category in categories if f"'{category}'" not in sql)
        assert missing == [], f"{path.name} is missing failure categories: {missing}"


def test_report_status_event_fresh_schema_declares_contract_columns() -> None:
    sql = (MIGRATIONS_DIR / "001_report_job_ledger.sql").read_text(encoding="utf-8")
    missing_columns = sorted(
        column for column in REPORT_STATUS_EVENT_CONTRACT_COLUMNS if column not in sql
    )

    assert missing_columns == []
    assert "idx_report_status_event_family_created" in sql
    assert "idx_report_status_event_idempotency_key" in sql


def test_report_job_work_queue_migration_enforces_lease_and_completion_shape() -> None:
    sql = (MIGRATIONS_DIR / "011_report_job_work_queue.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS report_job_work_item" in sql
    assert "UNIQUE REFERENCES report_job(report_job_id)" in sql
    assert "idx_report_job_work_runnable" in sql
    assert "idx_report_job_work_lease_expiry" in sql
    assert "status = 'leased'" in sql
    assert "status = 'completed' AND completed_at IS NOT NULL" in sql


def test_report_status_event_legacy_contract_preflight_runs_before_dependent_indexes() -> None:
    migration_paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
    migration_names = [path.name for path in migration_paths]

    assert migration_names.index(
        "000_report_status_event_legacy_contract_preflight.sql"
    ) < migration_names.index("001_report_job_ledger.sql")

    legacy_columns = {
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
    observed_columns = set(legacy_columns)
    dependent_index_checks = {
        "idx_report_status_event_family_created": "event_family",
        "idx_report_status_event_idempotency_key": "event_idempotency_key",
    }

    for path in migration_paths:
        sql = path.read_text(encoding="utf-8").lower()
        for column in REPORT_STATUS_EVENT_CONTRACT_COLUMNS:
            if f"add column if not exists {column}" in sql:
                observed_columns.add(column)
        for index_name, required_column in dependent_index_checks.items():
            if index_name in sql:
                assert required_column in observed_columns, (
                    f"{path.name} references {required_column} before the "
                    "legacy report_status_event contract preflight adds it."
                )


def test_report_status_event_upgrade_fixture_represents_pre_contract_schema() -> None:
    sql = LEGACY_UPGRADE_FIXTURE.read_text(encoding="utf-8").lower()

    assert "create table report_status_event" in sql
    assert "event-pre-contract-v0" in sql
    for column in REPORT_STATUS_EVENT_CONTRACT_COLUMNS:
        assert column not in sql
