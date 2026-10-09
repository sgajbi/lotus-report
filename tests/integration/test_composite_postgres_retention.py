"""Registered Report API and worker on real PostgreSQL; controlled Performance.

This checks Report custody and process reopening, not supplier or Excel acceptance.
The integration fixture provisions and drops a helper-owned database.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.reporting_jobs.models import ReportJobListFilters
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from tests.integration.postgres_adapter_ownership import own_postgres_adapter
from tests.unit.composite_reporting import test_source_products as product_cases
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    composite_lifecycle,
    exercise_registered_worker_package,
)
from tests.unit.composite_reporting.test_registered_lifecycle import (
    test_missing_month_is_failed_immutable_evidence_not_false_empty as exercise_missing_month,
)
from tests.unit.composite_reporting.test_registered_lifecycle import (
    test_order_worker_capture_retained_retrieval_and_two_tenant_isolation as exercise_retention,
)

READ_RETAINED = """
import json, os, sys
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
store = PostgresReportInputSnapshotStore(os.environ['REPORT_JOB_LEDGER_DATABASE_URL'])
try:
    snapshot = store.get_snapshot_by_job(sys.argv[1])
    print(json.dumps({'pid': os.getpid(), 'snapshot': snapshot.model_dump(mode='json')}))
finally:
    store.close()
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["original", "financial-correction"])
async def test_actual_source_products_postgres_capture_and_process_reopening(
    tmp_path, monkeypatch, version
):
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for actual PostgreSQL proof")

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url)),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url)),
        )

    packet = product_cases.load_source_packet()
    key = "composite-products-pg-" + uuid4().hex
    monkeypatch.setitem(HEADERS, "Idempotency-Key", key)
    await product_cases.test_registered_capture_retains_all_three_actual_sources_and_retrieval(
        packet,
        version,
        tmp_path,
        monkeypatch,
        adapters=adapters,
    )
    ledger, store = adapters()
    jobs = ledger.list_jobs(filters=ReportJobListFilters(tenant_id="tenant-a", idempotency_key=key))
    expected = store.get_snapshot_by_job(jobs[0].job_id)
    process = subprocess.run(
        [sys.executable, "-c", READ_RETAINED, expected.report_job_id],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    reopened = json.loads(process.stdout)
    assert reopened["pid"] != os.getpid()
    assert reopened["snapshot"] == expected.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_format", [None, "json", "xlsx"])
async def test_registered_postgres_capture_and_separate_process_retention(
    tmp_path, monkeypatch, missing_format
):
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for actual PostgreSQL proof")

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url)),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url)),
        )

    with composite_lifecycle(tmp_path, monkeypatch, adapters=adapters) as stack:
        key = "composite-pg-" + uuid4().hex
        monkeypatch.setitem(HEADERS, "Idempotency-Key", key)
        if missing_format is None:
            await exercise_retention(stack)
        else:
            await exercise_missing_month(stack, missing_format)
        ledger, store, worker, _ = stack
        await worker.run_once(worker_id="composite-pg-drain", max_items=10, lease_seconds=30)
        # Each parameter runs in the session database: locate this test's job
        # by its outcome, never assume an empty product database.
        jobs = ledger.list_jobs(
            filters=ReportJobListFilters(tenant_id="tenant-a", idempotency_key=key)
        )
        expected = store.get_snapshot_by_job(jobs[0].job_id)
        process = subprocess.run(
            [sys.executable, "-c", READ_RETAINED, expected.report_job_id],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert process.returncode == 0, process.stderr
        reopened = json.loads(process.stdout)
        assert reopened["pid"] != os.getpid()
        assert reopened["snapshot"] == expected.model_dump(mode="json")


@pytest.mark.asyncio
async def test_registered_postgres_xlsx_render_package(tmp_path, monkeypatch):
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for actual PostgreSQL proof")

    def adapters():
        return (
            own_postgres_adapter(PostgresReportJobLedger(database_url)),
            own_postgres_adapter(PostgresReportInputSnapshotStore(database_url)),
        )

    monkeypatch.setitem(HEADERS, "Idempotency-Key", "composite-xlsx-pg-" + uuid4().hex)
    packet = await exercise_registered_worker_package(tmp_path, monkeypatch, adapters=adapters)
    destination = os.environ.get("COMPOSITE_PRODUCER_PACKET_DIR")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {}
        for name, value in (
            ("render-package.json", packet["render_package"]),
            ("captured-source.json", packet["source_response"]),
            ("retained-snapshot.json", packet["snapshot"]),
            ("producer-packet.json", packet),
        ):
            content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
            (directory / name).write_bytes(content)
            manifest[name] = hashlib.sha256(content).hexdigest()
        repository = Path(__file__).resolve().parents[2]
        source_manifest = {
            path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((repository / "src/app").rglob("*.py"))
        }
        (directory / "artifact-manifest.json").write_text(
            json.dumps(
                {
                    "artifact_sha256": manifest,
                    "source_sha256": source_manifest,
                    "qualification": packet["qualification"],
                    "limits": packet["limits"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
