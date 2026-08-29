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


def _server_reachable(conninfo: str) -> bool:
    import psycopg

    try:
        with psycopg.connect(conninfo, connect_timeout=2):
            return True
    except psycopg.Error:
        return False


@pytest.fixture(scope="session", autouse=True)
def isolated_report_database() -> Iterator[None]:
    if os.environ.get(CALLER_OWNED_DATABASE_SENTINEL):
        # The caller vouches the database is already isolated (CI's ephemeral
        # service container, make ci's documented caller-owned contract, or
        # make ci-local's helper-owned database). Trust it: provisioning here
        # would nest a second temporary database and demand CREATEDB from
        # least-privilege roles that do not need it.
        yield
        return

    environment_conninfo = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    # The settings default IS the live local product DSN (port 5439), so an unset
    # environment variable does not mean "no database": factory-backed tests would
    # construct adapters against the running product stack. The effective value is
    # what must be isolated.
    source_conninfo = environment_conninfo or settings.report_job_ledger_database_url
    if not environment_conninfo and not _server_reachable(source_conninfo):
        # No caller-supplied database and nothing listening on the default. The
        # PostgreSQL-backed tests skip - but the probe is a moment in time, and a
        # server that comes up mid-session must not hand the product DSN to a
        # factory-backed test, so the cached settings are pointed at a DSN that can
        # never resolve for the session's duration.
        unreachable_placeholder = (
            "postgresql://lotus_report_isolated@localhost:1/lotus_report_unreachable"
        )
        settings.report_job_ledger_database_url = unreachable_placeholder
        get_postgres_connection_provider.cache_clear()
        try:
            yield
        finally:
            settings.report_job_ledger_database_url = source_conninfo
            get_postgres_connection_provider.cache_clear()
        return

    original_environment = environment_conninfo
    with provision_isolated_database(source_conninfo) as database:
        _rebind_database_url(database.target_conninfo)
        try:
            yield
        finally:
            try:
                close_postgres_connection_provider()
            except Exception:
                get_postgres_connection_provider.cache_clear()
            if original_environment is None:
                os.environ.pop("REPORT_JOB_LEDGER_DATABASE_URL", None)
                settings.report_job_ledger_database_url = source_conninfo
                get_postgres_connection_provider.cache_clear()
            else:
                _rebind_database_url(original_environment)
