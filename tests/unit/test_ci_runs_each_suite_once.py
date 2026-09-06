"""`make ci` must not run a test suite twice against one database.

`ci` once listed `test-integration` and `test-e2e` directly *and* invoked
`test-coverage`, which runs the same suites again for per-suite coverage. The
second session inherited the first one's committed rows and failed on batch
capacity it had itself consumed -- a local gate failing for a reason unrelated
to the change under test (issue #335).

Read from the Makefile rather than from `make -n`, so this states the intended
shape of the recipe instead of depending on a make binary being present.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"

#: Targets that execute a pytest suite directly. `test-coverage` reaches all
#: three through `test-suite-coverage`, so naming any of them alongside it runs
#: that suite twice.
DIRECT_SUITE_TARGETS = ("test-unit", "test-integration", "test-e2e")


def _ci_prerequisites() -> list[str]:
    """The prerequisite list of the `ci` target.

    Skips `ci: export VAR = value` lines, which are target-scoped variables
    rather than prerequisites, and would otherwise be read as one.
    """
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("ci:"):
            continue
        body = line[len("ci:") :].strip()
        if body.startswith("export "):
            continue
        return body.split()
    raise AssertionError("no ci target found in the Makefile")


def test_ci_does_not_run_any_suite_twice() -> None:
    prerequisites = _ci_prerequisites()
    assert "test-coverage" in prerequisites, (
        "ci is expected to run the suites through test-coverage; "
        f"prerequisites were {prerequisites}"
    )

    duplicated = [target for target in DIRECT_SUITE_TARGETS if target in prerequisites]
    assert duplicated == [], (
        "test-coverage already runs every suite, so listing these in ci runs them a "
        "second time against the same database, and the later session inherits the "
        f"earlier one's committed state: {duplicated}"
    )


def test_test_coverage_still_runs_all_three_suites() -> None:
    """The other half of the claim: removing them lost no coverage.

    Without this, the check above is satisfied by a `ci` that runs no tests at
    all.
    """
    makefile = MAKEFILE.read_text(encoding="utf-8")
    recipe = re.search(r"^test-coverage:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert recipe is not None, "no test-coverage recipe found"

    for suite in ("unit", "integration", "e2e"):
        assert f"TEST_SUITE={suite}" in recipe.group(1), (
            f"test-coverage no longer runs the {suite} suite: {recipe.group(1)}"
        )
