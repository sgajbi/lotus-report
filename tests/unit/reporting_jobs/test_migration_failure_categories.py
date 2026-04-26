from __future__ import annotations

from pathlib import Path
from typing import get_args

from app.reporting_jobs.models import ReportFailureCategory

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def test_report_job_failure_category_migrations_cover_model_vocabulary() -> None:
    categories = set(get_args(ReportFailureCategory))
    migration_paths = [
        MIGRATIONS_DIR / "001_report_job_ledger.sql",
        MIGRATIONS_DIR / "002_report_job_failure_category_and_operational_indexes.sql",
        MIGRATIONS_DIR / "005_report_job_render_lifecycle.sql",
        MIGRATIONS_DIR / "006_report_job_archive_handoff.sql",
    ]

    for path in migration_paths:
        sql = path.read_text(encoding="utf-8")
        missing = sorted(category for category in categories if f"'{category}'" not in sql)
        assert missing == [], f"{path.name} is missing failure categories: {missing}"
