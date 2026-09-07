"""Run the repository's mypy, refusing to run it from the wrong environment.

The pre-commit hook invokes mypy through the interpreter on `PATH`, which is
the only way to make the hook and CI the same checker. It also means a commit
from an unactivated shell resolves whichever interpreter happens to be there.

That case has to be refused rather than tolerated, because it does not fail on
its own:

* `mypy.ini` sets `ignore_missing_imports = True`, so an interpreter carrying
  mypy but not this project's dependencies resolves every project import to
  `Any`, finds nothing, and reports `Success: no issues found` having checked
  nothing.
* A different mypy, or a different Python, reports different diagnostics on
  the same tree. `pyproject.toml` pins mypy exactly for that reason, so an
  environment that merely *has* some mypy is not the checker CI runs.

Presence is therefore not the question. The three things that make this the
same checker are the interpreter version, the mypy version, and the dependency
graph -- so all three are asserted, and the first two are read from
`pyproject.toml` rather than restated here. A second copy of a pin is a pin
that drifts, and it would drift in the direction of silently passing.

What this does NOT establish, stated so it is not read as stronger than it is:
the third-party packages are checked for presence, not version. It cannot be
otherwise today, because nothing in this repository declares what version CI
type-checks against. `pyproject.toml` gives unbounded `>=` floors and `make
install` resolves them fresh on every run, so CI has no fixed answer either --
`fastapi` currently resolves to 0.141.1 against a `>=0.116.1` floor. An
environment satisfying the floors can therefore differ from CI's, and two CI
runs on the same tree can differ from each other. That is a dependency
determinism gap rather than a hook gap: until a lock or constraints set names
the versions, there is nothing for this guard to compare against. Tracked
separately; do not close it by hard-coding versions here, which would
reintroduce the drifting second copy this file avoids for mypy.
"""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

#: Distribution names stripped of extras and version specifiers, e.g.
#: `psycopg[binary]>=3.2.3` -> `psycopg`.
_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _runtime_distributions(project: dict) -> list[str]:
    """The runtime dependencies `pyproject.toml` declares, as distribution names.

    Derived rather than listed. This began as a hand-written tuple of seven
    import names and was wrong: `prometheus-client` was omitted, so an
    environment missing it passed the guard while mypy resolved the direct
    imports in `reporting_metrics.py` and `reporting_jobs/process.py` to `Any`
    and left every metric contract unchecked. Adding the eighth entry would
    have fixed that instance and left the next omission, because the defect was
    that the list was maintained by hand beside the file that already states
    the answer.

    Checked as distributions rather than importable modules so no second
    mapping is needed either: `pydantic-settings` and `prometheus-client`
    import under names that do not match their distribution, and inferring one
    from the other is a third place to be wrong.
    """

    names = []
    for requirement in project.get("dependencies", []):
        match = _REQUIREMENT_NAME.match(requirement.strip())
        if match:
            names.append(match.group(1))
    return names


def _project() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]


def _pinned_mypy(project: dict) -> str | None:
    """The exact mypy version `pyproject.toml` pins, or None if it stops pinning one.

    Searches the runtime dependencies and every optional group, because the
    pin lives under `[project.optional-dependencies] dev` rather than in the
    runtime list -- and hard-coding which table to look in would be one more
    thing that can drift away from the file it describes.
    """

    groups: list[list[str]] = [list(project.get("dependencies", []))]
    groups.extend(list(entries) for entries in project.get("optional-dependencies", {}).values())
    for requirement in (entry for group in groups for entry in group):
        match = re.fullmatch(r"mypy==([0-9][^\s;]*)", requirement.strip())
        if match:
            return match.group(1)
    return None


def _is_installed(distribution: str) -> bool:
    try:
        importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _minimum_python(project: dict) -> tuple[int, ...] | None:
    match = re.fullmatch(r">=\s*([0-9]+(?:\.[0-9]+)*)", str(project.get("requires-python", "")))
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _problems() -> list[str]:
    project = _project()
    problems: list[str] = []

    minimum = _minimum_python(project)
    if minimum is not None and sys.version_info[: len(minimum)] < minimum:
        running = ".".join(str(part) for part in sys.version_info[:3])
        problems.append(
            f"Python {running} is older than the required {'.'.join(str(part) for part in minimum)}"
        )

    pinned = _pinned_mypy(project)
    if pinned is None:
        problems.append("pyproject.toml no longer pins an exact mypy version for this to check")
    else:
        try:
            installed = importlib.metadata.version("mypy")
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"mypy is not installed (pyproject.toml pins {pinned})")
        else:
            if installed != pinned:
                problems.append(f"mypy {installed} is installed, but pyproject.toml pins {pinned}")

    declared = _runtime_distributions(project)
    if not declared:
        problems.append("pyproject.toml declares no runtime dependencies for this to check")
    missing = [name for name in declared if not _is_installed(name)]
    if missing:
        problems.append(
            "these declared dependencies are absent, and `ignore_missing_imports` "
            f"would make that a silent pass: {', '.join(missing)}"
        )

    return problems


def main() -> int:
    problems = _problems()
    if problems:
        print(
            "This is not the environment CI type-checks in, so its verdict would not "
            "mean what the hook claims.",
            file=sys.stderr,
        )
        print(f"  interpreter : {sys.executable}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Activate the project environment and commit again "
            "(see wiki/Getting-Started.md). Do not use --no-verify.",
            file=sys.stderr,
        )
        return 1

    return subprocess.call([sys.executable, "-m", "mypy", "--config-file", "mypy.ini"])


if __name__ == "__main__":
    raise SystemExit(main())
