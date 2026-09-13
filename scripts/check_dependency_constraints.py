"""Fail when the installed environment drifts from constraints.txt (report#345).

`make install` resolves fresh on every run, so two green runs of one tree can
be green against different dependency closures — nothing records what CI
built and type-checked against. The committed `constraints.txt` is the
reproducibility statement (the complete resolved closure); `pyproject.toml`
floors remain the compatibility statement. This gate makes the two agree with
the environment that actually ran: any installed distribution missing from
the constraints, any constrained version not installed, and any version
mismatch fails with the offending names.

Pure comparison core (`compare_constraints`) so the gate's refusal behavior
is unit-tested without touching an environment; the CLI binds it to the
running interpreter's installed distributions. No network, deterministic.

Refresh path (documented in the Makefile target): regenerate constraints in a
clean resolve, rerun the security audit against the new closure in the same
slice. The constraints file never becomes a vulnerability-exception
mechanism — that policy stays in
docs/standards/dependency-vulnerability-exceptions.md.

The recorded closure is LINUX-CI-OWNED: pip resolution honours platform
environment markers, so one committed exact closure cannot be simultaneously
true on the Ubuntu lanes and on a Windows workstation (pytest pulls colorama
only on win32, and freeze emits no markers to carry the difference). The
gate therefore ENFORCES on Linux — every lane that installs the project —
and reports not-evaluable without failing elsewhere, the same
platform-scoped posture as lotus-gateway's Ubuntu-only duplicate-code gate.
Refreshing the closure likewise happens in a Linux resolve (the Makefile
target runs it in the lane image), never from a workstation freeze.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

#: Distributions that are part of running Python/packaging itself rather than
#: the resolved closure; `pip freeze` excludes them, so the comparison must
#: too or a fresh runner image bump would read as project drift.
_TOOLING_DISTRIBUTIONS = frozenset({"pip", "setuptools", "wheel"})


def parse_constraints(text: str) -> dict[str, str]:
    """`name==version` per line; comments and blanks ignored.

    Anything not pinned exactly (ranges, extras syntax, editables, VCS refs)
    is a constraints-file defect and refuses: a reproducibility statement
    that permits a range reproduces nothing.
    """
    pinned: dict[str, str] = {}
    defects: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)", line)
        if match is None:
            defects.append(line)
            continue
        try:
            # A wildcard (for example `urllib3==1.*`) is a PEP 440 specifier,
            # not one concrete release. `pip-audit -r` would resolve it afresh,
            # recreating the very mutable audit target this closure prevents.
            pinned_version = str(Version(match.group(2)))
        except InvalidVersion:
            defects.append(line)
            continue
        pinned[canonical_name(match.group(1))] = pinned_version
    if defects:
        raise ValueError("dependency_constraints_not_exact:" + ",".join(defects[:10]))
    return pinned


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def compare_constraints(
    constrained: dict[str, str],
    installed: dict[str, str],
    *,
    project_name: str,
) -> list[str]:
    """Every difference between the two closures, stable order, empty = clean."""
    problems: list[str] = []
    project = canonical_name(project_name)
    installed_relevant = {
        name: version
        for name, version in installed.items()
        if name != project and name not in _TOOLING_DISTRIBUTIONS
    }
    for name in sorted(set(constrained) | set(installed_relevant)):
        pinned = constrained.get(name)
        actual = installed_relevant.get(name)
        if pinned is None:
            problems.append(f"unconstrained_installed:{name}=={actual}")
        elif actual is None:
            problems.append(f"constrained_not_installed:{name}=={pinned}")
        elif pinned != actual:
            problems.append(f"version_drift:{name}:constrained=={pinned}:installed=={actual}")
    return problems


def main() -> int:
    if not sys.platform.startswith("linux"):
        # Not a pass: an explicit not-evaluable, printed so a local run never
        # reads as certification. The closure is resolved for the lane
        # platform, and every blocking evaluation happens there.
        print(
            "Dependency constraints gate not evaluable on this platform: the "
            "recorded closure is resolved for the Linux CI lanes, which enforce "
            "it on every run (report#345)."
        )
        return 0
    constraints_path = Path("constraints.txt")
    if not constraints_path.exists():
        print(
            "Dependency constraints gate failed: constraints.txt is missing; "
            "the resolved closure is unrecorded (report#345).",
            file=sys.stderr,
        )
        return 1
    from importlib.metadata import distributions

    installed = {
        canonical_name(dist.metadata["Name"]): dist.version
        for dist in distributions()
        if dist.metadata["Name"]
    }
    try:
        constrained = parse_constraints(constraints_path.read_text(encoding="utf-8"))
    except ValueError as defect:
        print(f"Dependency constraints gate failed: {defect}", file=sys.stderr)
        return 1
    problems = compare_constraints(constrained, installed, project_name="lotus-report")
    if problems:
        print(
            "Dependency constraints gate failed: the installed environment is not "
            "the recorded closure:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"Dependency constraints gate passed ({len(constrained)} pinned distributions).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
