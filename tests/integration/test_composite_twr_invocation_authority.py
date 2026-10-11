"""Real owned PostgreSQL custody/restart; synthetic port, not native authority proof."""

import json
import os
import subprocess
import sys

import pytest

from app.composite_reporting.invocation_authority import binding_for_job
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from tests.integration.postgres_adapter_ownership import own_postgres_adapter
from tests.unit.composite_reporting.test_invocation_authority import (
    exercise_durable_binding,
    exercise_worker_retry,
)


def database_url():
    value = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not value:
        pytest.skip("Owned PostgreSQL required")
    return value


READ_JOB = """
import json, os, sys
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
ledger = PostgresReportJobLedger(os.environ['REPORT_JOB_LEDGER_DATABASE_URL'])
store = PostgresReportInputSnapshotStore(os.environ['REPORT_JOB_LEDGER_DATABASE_URL'])
try:
    job = ledger.get_job(sys.argv[1])
    result = {'pid': os.getpid(), 'job': job.model_dump(mode='json')}
    if len(sys.argv) > 2:
        result['snapshot'] = store.get_snapshot_by_job(sys.argv[1]).model_dump(mode='json')
    print(json.dumps(result))
finally:
    ledger.close()
    store.close()
"""


def read_from_fresh_process(job_id, expected, expected_snapshot=None):
    process = subprocess.run(
        [sys.executable, "-c", READ_JOB, job_id, *(["snapshot"] if expected_snapshot else [])],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    actual = json.loads(process.stdout)
    assert actual["pid"] != os.getpid()
    assert actual["job"] == expected.model_dump(mode="json")
    if expected_snapshot is not None:
        assert actual["snapshot"] == expected_snapshot.model_dump(mode="json")


def test_postgres_atomic_admission_idempotency_and_fresh_process_binding():
    ledger = own_postgres_adapter(PostgresReportJobLedger(database_url()))
    job = exercise_durable_binding(ledger)
    # This admission-only case owns its queued work; retire that exact item so
    # subsequent worker cases cannot consume another case's deliberately pending job.
    item = ledger.claim_work_items(worker_id="admission-proof", limit=1, lease_seconds=30)[0]
    assert item.report_job_id == job.job_id
    ledger.complete_work_item(work_item_id=item.work_item_id, lease_token=item.lease_token)
    ledger.close()
    reopened = own_postgres_adapter(PostgresReportJobLedger(database_url()))
    assert binding_for_job(reopened.get_job(job.job_id)) == binding_for_job(job)
    read_from_fresh_process(job.job_id, job)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["rotation", "revoked", "exhausted"])
async def test_postgres_worker_collecting_restart_current_authority_and_budget(
    tmp_path, monkeypatch, outcome
):
    from tests.unit.composite_reporting.test_registered_lifecycle import HEADERS

    monkeypatch.setitem(HEADERS, "Idempotency-Key", "pg-invocation-" + outcome)

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url())),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url())),
        )

    await exercise_worker_retry(
        tmp_path,
        monkeypatch,
        outcome,
        adapters=adapters,
        reopen_hook=read_from_fresh_process,
    )
