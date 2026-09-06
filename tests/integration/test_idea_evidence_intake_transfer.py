"""The populated-transfer proof report#326 has been waiting for.

Closed PR #332 claimed this and did not make it: it seeded a TEXT-typed table
before migrations and asserted a row survived, which `CREATE TABLE IF NOT
EXISTS` no-ops past. The state it seeded was also fictional, because the
pre-migration ledger is a SQLite *file*.

So these build a real SQLite intake ledger through the real writer, transfer
it, and check the thing an operator actually needs afterwards: that a replay of
a key accepted before the migration still returns its original receipt.

The `already_present` case carries the weight. `ON CONFLICT DO NOTHING` makes
re-running safe, and would just as happily skip a row whose key exists with
different content -- leaving a corrupted target that looks like a completed
transfer. The corruption test is what separates those two.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from app.idea_evidence_intake.models import IdeaEvidencePackIntakeRequest
from app.idea_evidence_intake.postgres_ledger import PostgresIdeaEvidenceIntakeLedger
from app.idea_evidence_intake.service import IdeaEvidenceIntakeLedger
from app.idea_evidence_intake.transfer import IntakeTransferError, transfer_intake_ledger
from app.reporting_jobs.models import ReportCallerContext
from app.reporting_persistence.schema import apply_report_schema_migrations

pytestmark = pytest.mark.integration


def _database_url() -> str:
    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for the intake transfer proof")
    return database_url


@pytest.fixture
def migrated_database_url() -> str:
    database_url = _database_url()
    with psycopg.connect(database_url) as connection:
        apply_report_schema_migrations(connection)
        connection.commit()
    return database_url


def _request(suffix: str, index: int) -> IdeaEvidencePackIntakeRequest:
    return IdeaEvidencePackIntakeRequest(
        report_evidence_pack_id=f"irep_{suffix}_{index}",
        conversion_intent_id=f"icnv_{suffix}_{index}",
        candidate_id=f"icand_{suffix}_{index}",
        purpose="CLIENT_REPORT_EVIDENCE",
        evidence_packet_id=f"ievp_{suffix}_{index}",
        evidence_content_fingerprint=f"sha256:idea-evidence-{index}",
        source_signal_ids=("sig_high_cash_001",),
        source_summaries=(
            {
                "product_id": "lotus-core:HoldingsAsOf:v1",
                "source_system": "lotus-core",
                "product_version": "v1",
                "as_of_date": "2026-06-24",
                "generated_at_utc": "2026-06-24T08:00:00Z",
                "data_quality_status": "complete",
                "freshness": "fresh",
            },
        ),
        reason_codes=("HIGH_CASH_REVIEWED_FOR_REPORT",),
        retention_policy_ref="generated-report-standard",
        requested_at_utc=datetime(2026, 6, 24, 8, 15, tzinfo=UTC),
    )


def _populated_sqlite_ledger(
    tmp_path, suffix: str, count: int = 5
) -> tuple[str, dict[str, object]]:
    """A real SQLite ledger, written through the real writer.

    Not hand-built rows: the point is to transfer what production would
    actually have, including whatever the writer chose to store.
    """
    path = tmp_path / "intake.sqlite3"
    ledger = IdeaEvidenceIntakeLedger(path)
    receipts: dict[str, object] = {}
    for index in range(count):
        key = f"intake-{suffix}-{index}"
        receipts[key] = ledger.accept(
            _request(suffix, index),
            idempotency_key=key,
            caller_context=ReportCallerContext(
                triggered_by="advisor-123",
                caller_application="lotus-idea",
                tenant_id="tenant-sg",
                region="APAC",
                booking_center_code="SG",
                role="advisor",
                correlation_id=f"corr-{suffix}-{index}",
                trace_id=f"trace-{suffix}-{index}",
            ),
            correlation_id=f"corr-{suffix}-{index}",
        )
    return str(path), receipts


def test_a_populated_ledger_transfers_and_still_replays(migrated_database_url, tmp_path) -> None:
    """The acceptance report#326 names: records carried, replay identity intact.

    The replay is the part that matters operationally. A transfer that moved
    rows but lost replay identity would leave every pre-migration request
    looking new, which is the failure the whole migration exists to avoid.
    """
    suffix = uuid4().hex[:12]
    sqlite_path, receipts = _populated_sqlite_ledger(tmp_path, suffix)

    report = transfer_intake_ledger(sqlite_path=sqlite_path, database_url=migrated_database_url)

    assert report.source_records == 5
    assert report.inserted == 5
    assert report.verified == 5
    assert report.complete

    postgres_ledger = PostgresIdeaEvidenceIntakeLedger(migrated_database_url)
    for index, (key, original) in enumerate(receipts.items()):
        replayed = postgres_ledger.accept(_request(suffix, index), idempotency_key=key)
        assert replayed == original, f"{key} did not replay to its pre-migration receipt"


def test_re_running_a_completed_transfer_changes_nothing(migrated_database_url, tmp_path) -> None:
    """An operator must be able to re-run it to confirm a cutover."""
    suffix = uuid4().hex[:12]
    sqlite_path, _ = _populated_sqlite_ledger(tmp_path, suffix)

    first = transfer_intake_ledger(sqlite_path=sqlite_path, database_url=migrated_database_url)
    second = transfer_intake_ledger(sqlite_path=sqlite_path, database_url=migrated_database_url)

    assert first.inserted == 5
    assert second.inserted == 0
    assert second.already_present == 5
    assert second.verified == 5


def test_an_interrupted_transfer_resumes(migrated_database_url, tmp_path) -> None:
    """A run killed half way leaves a prefix, and re-running completes it.

    Simulated by deleting part of the target after a full transfer, which is
    the same state an interruption leaves: some rows present, some absent.
    """
    suffix = uuid4().hex[:12]
    sqlite_path, _ = _populated_sqlite_ledger(tmp_path, suffix)
    transfer_intake_ledger(sqlite_path=sqlite_path, database_url=migrated_database_url)

    with psycopg.connect(migrated_database_url) as connection:
        connection.execute(
            "DELETE FROM idea_evidence_intake WHERE idempotency_key = ANY(%s)",
            ([f"intake-{suffix}-3", f"intake-{suffix}-4"],),
        )
        connection.commit()

    resumed = transfer_intake_ledger(sqlite_path=sqlite_path, database_url=migrated_database_url)

    assert resumed.inserted == 2
    assert resumed.already_present == 3
    assert resumed.verified == 5
    assert resumed.complete


def test_a_target_row_that_differs_fails_the_transfer(migrated_database_url, tmp_path) -> None:
    """The one that makes ON CONFLICT DO NOTHING safe to use.

    A key present with different content is skipped by the insert, so without
    content verification a corrupted target would report a clean transfer.
    """
    suffix = uuid4().hex[:12]
    sqlite_path, _ = _populated_sqlite_ledger(tmp_path, suffix)
    transfer_intake_ledger(sqlite_path=sqlite_path, database_url=migrated_database_url)

    corrupted_key = f"intake-{suffix}-2"
    with psycopg.connect(migrated_database_url) as connection:
        connection.execute(
            "UPDATE idea_evidence_intake SET payload_fingerprint = %s WHERE idempotency_key = %s",
            ("sha256:tampered", corrupted_key),
        )
        connection.commit()

    with pytest.raises(IntakeTransferError, match=corrupted_key):
        transfer_intake_ledger(sqlite_path=sqlite_path, database_url=migrated_database_url)


def test_a_missing_source_file_is_refused(migrated_database_url, tmp_path) -> None:
    """An absent ledger is a configuration error, not an empty transfer.

    Reporting success on a path that does not exist would let a cutover
    proceed having moved nothing.
    """
    with pytest.raises(IntakeTransferError, match="not found"):
        transfer_intake_ledger(
            sqlite_path=tmp_path / "absent.sqlite3",
            database_url=migrated_database_url,
        )
