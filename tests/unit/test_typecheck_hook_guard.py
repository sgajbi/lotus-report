"""The pre-commit mypy hook must refuse an environment that is not CI's.

The hook runs mypy through whatever interpreter is on `PATH`, which is what
makes it the same checker CI runs -- and also what lets a commit from an
unactivated shell run a different one. That case does not fail on its own:
`mypy.ini` sets `ignore_missing_imports`, so an interpreter with mypy but
without this project's dependencies reports `Success: no issues found` having
resolved every project import to `Any`.

The guard's whole value is in what it refuses, so that is what these assert.
Checking that a package is importable is not enough on its own either: a
populated Python 3.11 environment, or a globally installed mypy of another
version, would pass a presence check and then disagree with CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import run_typecheck_hook as hook  # noqa: E402


def test_the_real_environment_is_accepted() -> None:
    """Whatever else these assert, the guard must not refuse a correct setup.

    Without this the suite would pass just as happily against a guard that
    refuses everything, which is the failure mode of a check written only from
    its negative cases.
    """

    assert hook._problems() == []


def test_an_interpreter_below_requires_python_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook, "_minimum_python", lambda project: (99, 0))

    problems = hook._problems()

    assert len(problems) == 1
    assert "older than the required 99.0" in problems[0]


def test_a_mypy_other_than_the_pin_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A different mypy reports different diagnostics on the same tree.

    That is why `pyproject.toml` pins it exactly, and why merely having some
    mypy installed does not make this the checker CI runs.
    """

    monkeypatch.setattr(hook, "_pinned_mypy", lambda project: "0.0.1")

    problems = hook._problems()

    assert len(problems) == 1
    assert "pyproject.toml pins 0.0.1" in problems[0]


def test_losing_the_pin_is_refused_rather_than_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """If `pyproject.toml` stops pinning mypy, the check cannot be performed.

    A guard that quietly passes when its input disappears is the shape this
    whole change exists to remove, so absence of the pin is a refusal.
    """

    monkeypatch.setattr(hook, "_pinned_mypy", lambda project: None)

    problems = hook._problems()

    assert len(problems) == 1
    assert "no longer pins an exact mypy version" in problems[0]


def test_a_missing_typechecked_dependency_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hook,
        "REQUIRED_FOR_TYPECHECK",
        (*hook.REQUIRED_FOR_TYPECHECK, "a_package_no_environment_has"),
    )

    problems = hook._problems()

    assert len(problems) == 1
    assert "a_package_no_environment_has" in problems[0]
    assert "silent pass" in problems[0]


def test_the_pin_is_read_from_pyproject_rather_than_restated() -> None:
    """The version this guard enforces must be the one the project declares.

    A second copy of a pin is a pin that drifts, and it would drift toward
    passing: a guard checking a stale version accepts the environment it should
    refuse. Asserting the lookup finds the real pin is what keeps this file
    from becoming that second copy.
    """

    pinned = hook._pinned_mypy(hook._project())

    assert pinned is not None
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"mypy=={pinned}"' in pyproject


def test_the_pin_is_found_in_an_optional_group() -> None:
    """It lives under `[project.optional-dependencies] dev`, not the runtime list.

    Searching only `dependencies` returned `None` here, which the guard reads
    as "the project stopped pinning mypy" -- a refusal for the wrong reason,
    on every correct environment.
    """

    project = {"optional-dependencies": {"dev": ["ruff==0.16.4", "mypy==9.9.9"]}}

    assert hook._pinned_mypy(project) == "9.9.9"
