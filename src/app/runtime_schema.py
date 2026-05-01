import zlib
from contextlib import contextmanager
from typing import Iterator

import psycopg

from app.config import settings
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore

_SCHEMA_LOCK_KEY = zlib.crc32(b"lotus-report-runtime-schema")


@contextmanager
def _runtime_schema_lock(database_url: str) -> Iterator[None]:
    connection = psycopg.connect(database_url)
    try:
        connection.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_LOCK_KEY,))
        connection.commit()
        yield
    finally:
        try:
            connection.execute("SELECT pg_advisory_unlock(%s)", (_SCHEMA_LOCK_KEY,))
            connection.commit()
        finally:
            connection.close()


def ensure_runtime_schema() -> None:
    database_url = settings.report_job_ledger_database_url
    with _runtime_schema_lock(database_url):
        PostgresReportBatchLedger(database_url).check_ready()
        PostgresReportInputSnapshotStore(database_url).check_ready()


if __name__ == "__main__":
    ensure_runtime_schema()
