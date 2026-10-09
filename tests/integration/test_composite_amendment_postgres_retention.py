"""Actual Report PostgreSQL and registered ASGI; controlled Manage transport only."""

import json
import os
import subprocess
import sys

import pytest

from app.composite_reporting.amendment_contract import CompositeAmendmentReportData
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from tests.integration.postgres_adapter_ownership import own_postgres_adapter
from tests.integration.test_composite_postgres_retention import READ_RETAINED
from tests.unit.composite_reporting.test_monthly_amendment import exercise_amendment_lifecycle


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_amendment_registered_postgres_capture_and_fresh_process_retention(
    tmp_path, monkeypatch, version
):
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL required for actual PostgreSQL proof")

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url)),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url)),
        )

    retained = await exercise_amendment_lifecycle(tmp_path, monkeypatch, version, adapters=adapters)
    assert len(retained) == 2
    for job_id, expected in retained:
        process = subprocess.run(
            [sys.executable, "-c", READ_RETAINED, job_id],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert process.returncode == 0, process.stderr
        reopened = json.loads(process.stdout)
        assert reopened["pid"] != os.getpid()
        assert reopened["snapshot"] == expected
        # JSONB reorders object keys; retained custody must still revalidate without
        # changing the exact captured tables or ordered source arrays.
        payload = reopened["snapshot"]["snapshot_payload"]
        assert (
            CompositeAmendmentReportData.model_validate(payload).model_dump(mode="json") == payload
        )


@pytest.mark.asyncio
async def test_amendment_postgres_worker_refusal_retains_failed_evidence(tmp_path, monkeypatch):
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL required for actual PostgreSQL proof")

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url)),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url)),
        )

    retained = await exercise_amendment_lifecycle(
        tmp_path, monkeypatch, "v2", adapters=adapters, corrupt=True
    )
    job_id, expected = retained[0]
    process = subprocess.run(
        [sys.executable, "-c", READ_RETAINED, job_id],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    reopened = json.loads(process.stdout)
    assert reopened["pid"] != os.getpid() and reopened["snapshot"] == expected
