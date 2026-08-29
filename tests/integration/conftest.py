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

from scripts.run_isolated_ci import provision_isolated_database  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def isolated_report_database() -> Iterator[None]:
    source_conninfo = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not source_conninfo:
        yield
        return
    with provision_isolated_database(source_conninfo) as database:
        os.environ["REPORT_JOB_LEDGER_DATABASE_URL"] = database.target_conninfo
        try:
            yield
        finally:
            os.environ["REPORT_JOB_LEDGER_DATABASE_URL"] = source_conninfo
