"""Native PG package producer/reopening; controlled Manage and declined Render."""

import json
import os
import subprocess
import sys

import pytest

from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from tests.integration.postgres_adapter_ownership import own_postgres_adapter
from tests.unit.composite_reporting.amendment_examples import example
from tests.unit.composite_reporting.test_amendment_delivery import (
    exercise_package,
    two_month_example,
)

READ_PACKAGE = """
import json, os, sys
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_render.package_builder import _build_render_package
ledger = PostgresReportJobLedger(os.environ['REPORT_JOB_LEDGER_DATABASE_URL'])
store = PostgresReportInputSnapshotStore(os.environ['REPORT_JOB_LEDGER_DATABASE_URL'])
try:
    record = store.get_snapshot_by_job(sys.argv[1])
    package = _build_render_package(job=ledger.get_job(sys.argv[1]),
        snapshot=record.snapshot_payload, render_job_id='fresh-package',
        snapshot_id=record.snapshot_id, report_revision_id=record.report_revision_id,
        snapshot_record=record)
    print(json.dumps({'pid': os.getpid(), 'package': package}))
finally:
    store.close()
    ledger.close()
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["v1", "v2", "two-month"])
async def test_native_pg_package_replay_uses_frozen_snapshot_without_source(
    tmp_path, monkeypatch, shape
):
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL required for native PG package proof")

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url)),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url)),
        )

    cases = (
        [two_month_example()]
        if shape == "two-month"
        else [example(shape, revision) for revision in (2, 3)]
    )
    retained = await exercise_package(tmp_path, monkeypatch, cases, adapters=adapters)
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
        read = json.loads(result.stdout)
        assert read["pid"] != os.getpid()
        assert (
            read["package"]["report_data"] == record["snapshot_payload"] == expected["report_data"]
        )
        assert read["package"]["render_context"] == expected["render_context"]
        assert read["package"]["template_version"] == "v6"
