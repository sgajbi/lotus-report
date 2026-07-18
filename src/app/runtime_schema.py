import sys
import zlib
from contextlib import contextmanager
from typing import Iterator

from app.config import settings
from app.postgres import PostgresConnectionProvider
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_persistence import ReportSchemaError

_SCHEMA_LOCK_KEY = zlib.crc32(b"lotus-report-runtime-schema")


@contextmanager
def _runtime_schema_lock(connection_provider: PostgresConnectionProvider) -> Iterator[None]:
    with connection_provider.connection() as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_LOCK_KEY,))
        connection.commit()
        try:
            yield
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (_SCHEMA_LOCK_KEY,))
            connection.commit()


def ensure_runtime_schema() -> None:
    connection_provider = PostgresConnectionProvider.from_settings(settings)
    try:
        with _runtime_schema_lock(connection_provider):
            PostgresReportBatchLedger(connection_provider=connection_provider).check_ready()
            PostgresReportInputSnapshotStore(connection_provider=connection_provider).check_ready()
    finally:
        connection_provider.close()


def main() -> int:
    try:
        ensure_runtime_schema()
    except ReportSchemaError as exc:
        print(f"lotus_report_schema_startup_failed:{exc}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
