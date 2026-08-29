"""Integration tests always run against a helper-owned, session-scoped database.

The local product stack runs a batch worker that scans and mutates the product
database every five seconds. Sharing that database made local integration runs
non-deterministic: the worker dispatched batches the tests had just created and
were about to assert on (issue #179, mirror of lotus-gateway#585).

This fixture applies the isolation `make ci-local` already had to every way of
running the integration suite: when REPORT_JOB_LEDGER_DATABASE_URL names a
PostgreSQL server, the session provisions `<dbname>_ci_<token>` on that server,
points the suite at it, and drops it afterwards. Tests therefore never connect
to the database the worker containers write to - a running product stack is
neither a prerequisite nor a hazard. When the variable is unset, the
PostgreSQL-backed tests skip exactly as before.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402
from app.postgres import (  # noqa: E402
    close_postgres_connection_provider,
    get_postgres_connection_provider,
)
from scripts.run_isolated_ci import (  # noqa: E402
    CALLER_OWNED_DATABASE_SENTINEL,
    provision_isolated_database,
)


def _rebind_database_url(target_conninfo: str) -> None:
    """Point every configuration surface at the given database.

    The module-level `settings` object captures the DSN at import, and the pooled
    connection provider is lru-cached from it - patching os.environ alone would
    leave any code path reading those still on the product database.
    """
    os.environ["REPORT_JOB_LEDGER_DATABASE_URL"] = target_conninfo
    settings.report_job_ledger_database_url = target_conninfo
    get_postgres_connection_provider.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def isolated_report_database() -> Iterator[None]:
    source_conninfo = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not source_conninfo:
        yield
        return
    if os.environ.get(CALLER_OWNED_DATABASE_SENTINEL):
        # The caller vouches the database is already isolated (CI's ephemeral
        # service container, or make ci-local's helper-owned database). Trust it:
        # provisioning here would nest a second temporary database and demand
        # CREATEDB from least-privilege roles that do not need it.
        yield
        return
    with provision_isolated_database(source_conninfo) as database:
        _rebind_database_url(database.target_conninfo)
        try:
            yield
        finally:
            try:
                close_postgres_connection_provider()
            except Exception:
                get_postgres_connection_provider.cache_clear()
            _rebind_database_url(source_conninfo)
