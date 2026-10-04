"""Real PG and a separate-process retained read; Risk/Render/Archive remain controlled seams."""

import json
import os
import subprocess
import sys

import pytest

from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from tests.integration.postgres_adapter_ownership import own_postgres_adapter
from tests.unit.services.test_risk_qualification_retention import (
    test_capture_reopen_replay_and_rerender_retain_risk_qualification as exercise_retention,
)

READ_RETAINED = """
import json, os, sys
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
store = PostgresReportInputSnapshotStore(os.environ['REPORT_JOB_LEDGER_DATABASE_URL'])
try:
    snapshot = store.get_snapshot_by_job(sys.argv[1])
    print(json.dumps({'pid': os.getpid(), 'snapshot_id': snapshot.snapshot_id,
                     'payload': snapshot.snapshot_payload, 'hash': snapshot.snapshot_hash,
                     'revision': snapshot.report_revision_id}))
finally:
    store.close()
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,reason",
    [
        ("ready", "calculation_complete"),
        ("stale", "stale_source_observations"),
        ("degraded", "calculation_quality_issue"),
    ],
)
@pytest.mark.parametrize("limited_route", ["calculate", "rolling-metrics"])
async def test_postgres_capture_process_read_and_retained_render_reuse(
    tmp_path, monkeypatch, state, reason, limited_route
):
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for actual PostgreSQL proof")

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url)),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url)),
        )

    def verify_retained(job_id, expected):
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
        process = subprocess.run(
            [sys.executable, "-c", READ_RETAINED, job_id],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert process.returncode == 0, process.stderr
        actual = json.loads(process.stdout)
        assert actual["pid"] != os.getpid()
        assert actual["snapshot_id"] == expected.snapshot_id
        assert actual["payload"] == expected.snapshot_payload
        assert actual["hash"] == expected.snapshot_hash
        assert actual["revision"] == expected.report_revision_id is not None

    await exercise_retention(
        tmp_path,
        monkeypatch,
        state,
        reason,
        limited_route,
        adapters=adapters,
        verify_retained=verify_retained,
    )
