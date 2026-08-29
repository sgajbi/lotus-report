"""Run the repository CI contract against an owned temporary PostgreSQL database."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

_DATABASE_NAME_LIMIT = 63
_TOKEN_LENGTH = 12
_ADMIN_DATABASE = "postgres"

# Set truthy when REPORT_JOB_LEDGER_DATABASE_URL already names a caller-owned isolated
# database (CI's ephemeral service container, or this helper's own child run), so the
# integration-test session does not provision a second nested database.
CALLER_OWNED_DATABASE_SENTINEL = "REPORT_JOB_LEDGER_DATABASE_IS_ISOLATED"


class IsolatedCiConfigurationError(ValueError):
    """Raised when local CI cannot derive a safe database lifecycle."""


@dataclass(frozen=True)
class IsolatedCiDatabase:
    """Connection material for one helper-owned local CI database."""

    source_database: str
    database_name: str
    admin_conninfo: str
    target_conninfo: str


def build_isolated_ci_database(source_conninfo: str, *, token: str) -> IsolatedCiDatabase:
    """Derive admin and target connection strings without mutating the source database."""

    if not source_conninfo.strip():
        raise IsolatedCiConfigurationError(
            "REPORT_JOB_LEDGER_DATABASE_URL must identify a reachable PostgreSQL server"
        )
    if not re.fullmatch(r"[a-f0-9]+", token):
        raise IsolatedCiConfigurationError("the generated database token is invalid")

    try:
        source_parameters = conninfo_to_dict(source_conninfo)
    except psycopg.Error as exc:
        raise IsolatedCiConfigurationError(
            "REPORT_JOB_LEDGER_DATABASE_URL is not a valid PostgreSQL connection string"
        ) from exc

    source_database = source_parameters.get("dbname")
    if not source_database:
        raise IsolatedCiConfigurationError(
            "REPORT_JOB_LEDGER_DATABASE_URL must include an explicit database name"
        )

    safe_source_name = re.sub(r"[^a-z0-9_]", "_", source_database.lower()).strip("_")
    if not safe_source_name:
        safe_source_name = "lotus_report"
    suffix = f"_ci_{token}"
    database_name = f"{safe_source_name[: _DATABASE_NAME_LIMIT - len(suffix)]}{suffix}"
    if database_name == source_database:
        raise IsolatedCiConfigurationError("the isolated database must differ from the source")

    admin_parameters = dict(source_parameters)
    admin_parameters["dbname"] = _ADMIN_DATABASE
    admin_parameters["application_name"] = "lotus-report-ci-local-admin"

    target_parameters = dict(source_parameters)
    target_parameters["dbname"] = database_name
    target_parameters["application_name"] = "lotus-report-ci-local"

    return IsolatedCiDatabase(
        source_database=source_database,
        database_name=database_name,
        admin_conninfo=make_conninfo(**admin_parameters),
        target_conninfo=make_conninfo(**target_parameters),
    )


def _create_database(database: IsolatedCiDatabase) -> None:
    with psycopg.connect(database.admin_conninfo, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database.database_name))
            )


def _drop_database(database: IsolatedCiDatabase) -> None:
    with psycopg.connect(database.admin_conninfo, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (database.database_name,),
            )
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database.database_name))
            )


@contextmanager
def provision_isolated_database(source_conninfo: str) -> Iterator[IsolatedCiDatabase]:
    """Create a helper-owned database for the caller's lifetime, dropping it afterwards.

    The database name is derived from the source name plus a random token, so it can
    never be the product database the local worker containers write to - the same
    isolation `make ci-local` uses, packaged for the integration-test session
    (issue #179).
    """

    database = build_isolated_ci_database(
        source_conninfo,
        token=secrets.token_hex(_TOKEN_LENGTH // 2),
    )
    _create_database(database)
    try:
        yield database
    finally:
        _drop_database(database)


def run_isolated_ci(
    source_conninfo: str,
    *,
    token: str | None = None,
    environ: Mapping[str, str] | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> int:
    """Run ``make ci`` with a helper-owned database and guaranteed cleanup."""

    database = build_isolated_ci_database(
        source_conninfo,
        token=token or secrets.token_hex(_TOKEN_LENGTH // 2),
    )
    make_executable = shutil.which("make")
    if make_executable is None:
        raise IsolatedCiConfigurationError("make is required to run the local CI contract")

    child_environment = dict(environ or os.environ)
    child_environment["REPORT_JOB_LEDGER_DATABASE_URL"] = database.target_conninfo
    child_environment[CALLER_OWNED_DATABASE_SENTINEL] = "true"

    _create_database(database)
    print(f"Created isolated local CI database '{database.database_name}'.")
    try:
        completed = command_runner(
            [make_executable, "ci"],
            check=False,
            env=child_environment,
        )
        return completed.returncode
    finally:
        _drop_database(database)
        print(f"Dropped isolated local CI database '{database.database_name}'.")


def main() -> int:
    source_conninfo = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL", "")
    try:
        return run_isolated_ci(source_conninfo)
    except IsolatedCiConfigurationError as exc:
        print(f"Local CI configuration error: {exc}", file=sys.stderr)
        return 2
    except psycopg.Error as exc:
        print(
            f"Local CI database lifecycle failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
