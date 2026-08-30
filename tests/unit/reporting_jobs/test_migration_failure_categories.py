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


def test_backfill_migration_recovers_stranded_artifactless_failures() -> None:
    """Migration 013 must convert rows that already failed under the old
    archive_validation_failed posture (matched by the exact message only that
    code path wrote) into the retry-eligible render_artifact_unrecoverable
    category, for both report jobs and rerender attempts - and must stay free
    of semicolons inside string literals because the migration runner splits
    statements on ';'."""

    sql = (
        MIGRATIONS_DIR / "013_report_failure_category_render_artifact_unrecoverable.sql"
    ).read_text(encoding="utf-8")
    old_message = "Rendered artifact payload was not available for archive handoff."
    for table in ("report_job", "report_rerender_attempt"):
        assert f"UPDATE {table}" in sql
    assert sql.count(f"AND failure_message = '{old_message}'") == 2
    assert sql.count("retry_eligible = TRUE") == 2
    for statement in sql.split(";"):
        assert statement.count("'") % 2 == 0, "semicolon inside a string literal"


def test_archive_execution_failed_backfill_is_category_scoped() -> None:
    """Migration 014 converts the rows stranded under the old non-retryable
    posture. The category has a single producer, so the category-scoped
    predicate is precise; the retry_eligible = FALSE guard makes re-applies
    no-ops, and no string literal may contain a semicolon (the runner splits
    statements on it)."""

    sql = (MIGRATIONS_DIR / "014_report_archive_execution_failed_retryable.sql").read_text(
        encoding="utf-8"
    )
    for table in ("report_job", "report_rerender_attempt"):
        assert f"UPDATE {table}" in sql
    assert sql.count("SET retry_eligible = TRUE") == 2
    assert sql.count("AND retry_eligible = FALSE") == 2
    assert "failure_message" not in sql
    for statement in sql.split(";"):
        assert statement.count("'") % 2 == 0
