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


def test_a_missing_declared_dependency_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bound before patching: referring to `hook._runtime_distributions` inside
    # the replacement would resolve to the replacement itself.
    declared = hook._runtime_distributions
    monkeypatch.setattr(
        hook,
        "_runtime_distributions",
        lambda project: [*declared(project), "a-package-no-environment-has"],
    )

    problems = hook._problems()

    assert len(problems) == 1
    assert "a-package-no-environment-has" in problems[0]
    assert "silent pass" in problems[0]


def test_losing_the_dependency_list_is_refused_rather_than_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty derived list must not read as "everything present"."""

    monkeypatch.setattr(hook, "_runtime_distributions", lambda project: [])

    problems = hook._problems()

    assert len(problems) == 1
    assert "declares no runtime dependencies" in problems[0]


def test_every_declared_runtime_dependency_is_checked() -> None:
    """The set checked is the set declared, not a copy someone maintains.

    This started as a hand-written tuple of seven import names and omitted
    `prometheus-client`, so an environment without it passed the guard while
    mypy resolved `reporting_metrics.py`'s direct imports to `Any` and left
    every metric contract unchecked. Adding an eighth entry would have fixed
    that instance and left the next omission.
    """

    project = hook._project()
    declared = {
        requirement.split("[")[0].split(">")[0].split("=")[0].strip()
        for requirement in project["dependencies"]
    }

    assert set(hook._runtime_distributions(project)) == declared
    assert "prometheus-client" in declared, "the dependency the hand-written list missed"


def test_extras_and_specifiers_are_stripped_from_distribution_names() -> None:
    """`psycopg[binary]>=3.2.3` names the distribution `psycopg`.

    Left unstripped, every lookup would miss and the guard would refuse every
    correct environment -- the failure the optional-group bug already produced
    once in this file.
    """

    project = {"dependencies": ["psycopg[binary]>=3.2.3", "uvicorn[standard]>=0.35.0", "httpx"]}

    assert hook._runtime_distributions(project) == ["psycopg", "uvicorn", "httpx"]


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


@pytest.mark.parametrize(
    "spelling",
    [
        "mypy==2.3.1",
        "mypy == 2.3.1",  # PEP 508 permits whitespace around the operator
        'mypy==2.3.1 ; python_version >= "3.12"',  # environment marker
        "mypy[faster-cache]==2.3.1",  # extra
        "MyPy==2.3.1",  # PEP 503 name normalisation
    ],
)
def test_every_pep508_spelling_of_the_same_pin_is_read(spelling: str) -> None:
    r"""The pin must be found however it is legally written.

    The hand-rolled `mypy==([0-9][^\s;]*)` this replaced matched only the first
    of these. Every spelling it missed returned `None`, which the guard reads as
    "the project stopped pinning mypy" and refuses -- so reformatting
    `mypy==2.3.1` to `mypy == 2.3.1` would have blocked every commit in the
    repository, blaming `pyproject.toml` for something it had not done.

    Found by `lotus-platform-51`, who hit the same class in a pin regex that
    rejected `ruff == 0.15.22` and `2.3.1.post1`.
    """

    assert hook._pinned_mypy({"dependencies": [spelling]}) == "2.3.1"


@pytest.mark.parametrize("spelling", ["mypy>=2.3.1", "mypy", "mypy<3", "mypy!=2.0"])
def test_a_requirement_that_is_not_an_exact_pin_is_not_read_as_one(spelling: str) -> None:
    """Only `==` is a pin. A floor or a range is the absence of one, and the
    guard must refuse rather than invent a version to compare against."""

    assert hook._pinned_mypy({"dependencies": [spelling]}) is None


def test_a_malformed_requirement_does_not_crash_the_guard() -> None:
    """A guard that raises on unparseable input fails the commit for the wrong
    reason and reports nothing useful."""

    project = {"dependencies": ["!!!not a requirement!!!", "mypy==2.3.1"]}

    assert hook._pinned_mypy(project) == "2.3.1"


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        (">=3.12", (3, 12)),
        (">= 3.12", (3, 12)),
        (">=3.12,<4.0", (3, 12)),  # the regex this replaced matched nothing here
        (">=3.12.0", (3, 12, 0)),  # every component kept, see the patch-floor test
        ("==3.12.*", None),  # a valid specifier, not a version to compare against
        (">3.11", None),
        ("", None),
        ("garbage", None),
    ],
)
def test_requires_python_lower_bound_is_read_from_the_specifier(
    declaration: str, expected: tuple[int, ...] | None
) -> None:
    """`>=3.12,<4.0` is an ordinary declaration and the previous regex, which
    assumed the whole value was a lone `>=`, silently disabled the interpreter
    check on it rather than failing."""

    assert hook._minimum_python({"requires-python": declaration}) == expected


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        (">=3.12", (3, 12)),
        (">=3.12.7", (3, 12, 7)),  # a patch floor must not truncate to (3, 12)
        (">=3.12.7,<4.0", (3, 12, 7)),
    ],
)
def test_a_patch_level_floor_keeps_its_patch_component(
    declaration: str, expected: tuple[int, ...]
) -> None:
    """`>=3.12.7` truncated to `(3, 12)` accepts 3.12.0 through 3.12.6.

    Those interpreters do not satisfy the project metadata, and the regex this
    replaced kept every numeric component -- so losing precision here would be
    a regression introduced by the standards-aware parser.
    """

    assert hook._minimum_python({"requires-python": declaration}) == expected


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ('mypy==2.3.1 ; python_version >= "3.12"', "2.3.1"),  # marker true here
        ('mypy==2.3.1 ; python_version < "3"', None),  # marker false here
        ('mypy==9.9.9 ; python_version < "3"', None),
    ],
)
def test_a_pin_whose_marker_is_false_here_is_not_the_pin_in_force(
    requirement: str, expected: str | None
) -> None:
    """`pip install -e ".[dev]"` does not select a requirement whose marker is
    false, so its specifier is not the pin this environment must match.

    Without this, a stale version-partitioned entry satisfies the check against
    a mypy the project would never install on this interpreter.
    """

    assert hook._pinned_mypy({"dependencies": [requirement]}) == expected


def test_dependency_names_survive_extras_markers_and_spacing() -> None:
    project = {
        "dependencies": [
            "psycopg[binary]>=3.2.3",
            "uvicorn [standard] >= 0.35.0",
            'httpx>=0.28.1 ; python_version >= "3.12"',
            "!!!broken!!!",
        ]
    }

    assert hook._runtime_distributions(project) == ["psycopg", "uvicorn", "httpx"]


def test_the_pin_is_found_in_an_optional_group() -> None:
    """It lives under `[project.optional-dependencies] dev`, not the runtime list.

    Searching only `dependencies` returned `None` here, which the guard reads
    as "the project stopped pinning mypy" -- a refusal for the wrong reason,
    on every correct environment.
    """

    project = {"optional-dependencies": {"dev": ["ruff==0.16.4", "mypy==9.9.9"]}}

    assert hook._pinned_mypy(project) == "9.9.9"
