"""Existing PG adapters and fresh-process source-free replay for changed v7 source."""

import json
import os
import subprocess
import sys

import pytest

from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from tests.integration.postgres_adapter_ownership import own_postgres_adapter
from tests.integration.test_composite_amendment_package_retention import READ_PACKAGE
from tests.unit.composite_reporting.test_historical_delivery import exercise_historical_delivery


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", ["v1", "v2"])
@pytest.mark.parametrize("published", [False, True])
async def test_historical_pg_capture_and_fresh_process_package(
    tmp_path, monkeypatch, definition, published
):
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL required for native PostgreSQL proof")

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url)),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url)),
        )

    retained = await exercise_historical_delivery(
        tmp_path, monkeypatch, definition, published, adapters=adapters
    )
    for job_id, record, expected in retained:
        result = subprocess.run(
            [sys.executable, "-c", READ_PACKAGE, job_id],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        reopened = json.loads(result.stdout)
        assert reopened["pid"] != os.getpid()
        assert (
            reopened["package"]["report_data"]
            == record["snapshot_payload"]
            == expected["report_data"]
        )
        assert reopened["package"]["render_context"] == expected["render_context"]
        assert reopened["package"]["template_version"] == "v7"
