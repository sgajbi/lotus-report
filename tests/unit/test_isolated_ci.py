from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from scripts import run_isolated_ci

SOURCE_DSN = "postgresql://report_user:secret@127.0.0.1:5439/lotus-report-prod"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_make_ci_local_routes_through_the_isolation_helper() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "ci-local: ci" not in makefile
    assert "ci-local:\n\tpython scripts/run_isolated_ci.py" in makefile


def test_make_ci_runs_each_suite_once_through_combined_coverage() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    ci_header = next(line for line in makefile.splitlines() if line.startswith("ci: lint"))
    prerequisites = ci_header.removeprefix("ci:").split()

    assert "test-coverage" in prerequisites
    assert "test-unit" not in prerequisites
    assert "test-integration" not in prerequisites
    assert "test-e2e" not in prerequisites


def test_build_isolated_ci_database_separates_and_bounds_database_identity() -> None:
    database = run_isolated_ci.build_isolated_ci_database(
        SOURCE_DSN,
        token="abcdef012345",
    )

    assert database.source_database == "lotus-report-prod"
    assert database.database_name == "lotus_report_prod_ci_abcdef012345"
    assert database.database_name != database.source_database
    assert len(database.database_name) <= 63
    assert conninfo_to_dict(database.admin_conninfo)["dbname"] == "postgres"
    assert conninfo_to_dict(database.target_conninfo)["dbname"] == database.database_name


@pytest.mark.parametrize(
    ("source_conninfo", "message"),
    [
        ("", "must identify a reachable PostgreSQL server"),
        ("host=127.0.0.1 user=report_user", "explicit database name"),
    ],
)
def test_build_isolated_ci_database_rejects_unsafe_configuration(
    source_conninfo: str,
    message: str,
) -> None:
    with pytest.raises(run_isolated_ci.IsolatedCiConfigurationError, match=message):
        run_isolated_ci.build_isolated_ci_database(
            source_conninfo,
            token="abcdef012345",
        )


def test_run_isolated_ci_cleans_up_after_success(monkeypatch, capsys) -> None:
    lifecycle: list[tuple[str, str]] = []
    observed_environment: dict[str, str] = {}

    monkeypatch.setattr(run_isolated_ci.shutil, "which", lambda _command: "make")
    monkeypatch.setattr(
        run_isolated_ci,
        "_create_database",
        lambda database: lifecycle.append(("create", database.database_name)),
    )
    monkeypatch.setattr(
        run_isolated_ci,
        "_drop_database",
        lambda database: lifecycle.append(("drop", database.database_name)),
    )

    def command_runner(command, *, check, env):
        assert command == ["make", "ci"]
        assert check is False
        observed_environment.update(env)
        return subprocess.CompletedProcess(command, 0)

    return_code = run_isolated_ci.run_isolated_ci(
        SOURCE_DSN,
        token="abcdef012345",
        environ={"EXISTING_SETTING": "preserved"},
        command_runner=command_runner,
    )

    target_parameters = conninfo_to_dict(observed_environment["REPORT_JOB_LEDGER_DATABASE_URL"])
    assert return_code == 0
    assert observed_environment["EXISTING_SETTING"] == "preserved"
    assert target_parameters["dbname"] == "lotus_report_prod_ci_abcdef012345"
    assert lifecycle == [
        ("create", "lotus_report_prod_ci_abcdef012345"),
        ("drop", "lotus_report_prod_ci_abcdef012345"),
    ]
    output = capsys.readouterr().out
    assert "secret" not in output
    assert SOURCE_DSN not in output


def test_run_isolated_ci_cleans_up_after_child_gate_failure(monkeypatch) -> None:
    lifecycle: list[str] = []

    monkeypatch.setattr(run_isolated_ci.shutil, "which", lambda _command: "make")
    monkeypatch.setattr(
        run_isolated_ci,
        "_create_database",
        lambda _database: lifecycle.append("create"),
    )
    monkeypatch.setattr(
        run_isolated_ci,
        "_drop_database",
        lambda _database: lifecycle.append("drop"),
    )

    return_code = run_isolated_ci.run_isolated_ci(
        SOURCE_DSN,
        token="abcdef012345",
        command_runner=lambda command, **_kwargs: subprocess.CompletedProcess(command, 7),
    )

    assert return_code == 7
    assert lifecycle == ["create", "drop"]


def test_run_isolated_ci_cleans_up_after_child_process_error(monkeypatch) -> None:
    lifecycle: list[str] = []

    monkeypatch.setattr(run_isolated_ci.shutil, "which", lambda _command: "make")
    monkeypatch.setattr(
        run_isolated_ci,
        "_create_database",
        lambda _database: lifecycle.append("create"),
    )
    monkeypatch.setattr(
        run_isolated_ci,
        "_drop_database",
        lambda _database: lifecycle.append("drop"),
    )

    def command_runner(*_args, **_kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_isolated_ci.run_isolated_ci(
            SOURCE_DSN,
            token="abcdef012345",
            command_runner=command_runner,
        )

    assert lifecycle == ["create", "drop"]


def test_provision_isolated_database_creates_then_always_drops(monkeypatch) -> None:
    """The session-scoped integration fixture rides on this contract (issue #179)."""

    events: list[str] = []
    monkeypatch.setattr(
        run_isolated_ci,
        "_create_database",
        lambda database: events.append(f"create:{database.database_name}"),
    )
    monkeypatch.setattr(
        run_isolated_ci,
        "_drop_database",
        lambda database: events.append(f"drop:{database.database_name}"),
    )

    with run_isolated_ci.provision_isolated_database(
        "postgresql://lotus_report:pw@localhost:5439/lotus_report"
    ) as database:
        assert database.database_name != "lotus_report"
        assert database.database_name.startswith("lotus_report_ci_")
        assert events == [f"create:{database.database_name}"]

    assert events == [
        f"create:{database.database_name}",
        f"drop:{database.database_name}",
    ]


def test_provision_isolated_database_drops_even_when_the_body_raises(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        run_isolated_ci,
        "_create_database",
        lambda database: events.append("create"),
    )
    monkeypatch.setattr(
        run_isolated_ci,
        "_drop_database",
        lambda database: events.append("drop"),
    )

    with pytest.raises(RuntimeError):
        with run_isolated_ci.provision_isolated_database(
            "postgresql://lotus_report:pw@localhost:5439/lotus_report"
        ):
            raise RuntimeError("session failed")

    assert events == ["create", "drop"]
