"""Every declared gate must be reachable from a blocking lane.

`.PHONY` declares that a target is not a file. It is not an invocation, and documentation telling a
developer to run a command is an instruction to a human rather than enforcement. Four targets in
this repository were declared, documented, and invoked by nothing:

    domain-product-validate                        (issue #182)
    idea-evidence-intake-contract-gate
    idea-evidence-materialization-contract-gate
    monetary-float-guard

All four passed when run by hand, so nothing was hidden here. That is luck rather than design: one
repository over, `lotus-ai` declared `monetary-float-guard`, invoked it from nothing, and it failed
with five findings nobody had seen (`lotus-ai#164`). **A dead gate is not neutral - it is nothing
checking while everyone assumes something is.**

The reachability check is what was missing, not the gates. Wiring the four in fixes today; this
fixes the class, so a fifth cannot arrive dead.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"

BLOCKING_LANES = ("check", "ci")

# A target whose name claims it enforces something.
_GATE_NAME = re.compile(r"^([a-z][a-z0-9-]*(?:-gate|-gates|-guard|-validate)):", re.M)

# Targets that legitimately run outside `check`/`ci`, each with the reason. An entry here is a
# decision; the absence of an entry is what this check treats as a defect.
DISPOSITIONED = {
    # Runs in the coverage workflow steps against combined coverage data, which `check` does not
    # produce. Reachable from CI, just not through the aggregate lanes.
    "coverage-gate": "invoked directly by the coverage workflow steps",
}


def _makefile() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _declared_gate_targets() -> set[str]:
    targets = {match.group(1) for match in _GATE_NAME.finditer(_makefile())}
    assert targets, (
        "No gate-shaped targets were found in the Makefile. Either they were all renamed - in "
        "which case this check needs updating - or this pattern stopped matching, in which case "
        "it asserts nothing. Both are failures."
    )
    return targets


def _reachable_from_lanes() -> set[str]:
    """Targets reachable from `check` or `ci`, expanding aggregates transitively."""

    makefile = _makefile()
    reachable: set[str] = set()
    frontier = []
    for lane in BLOCKING_LANES:
        match = re.search(rf"^{lane}: (.+)$", makefile, re.M)
        assert match is not None, f"The `{lane}` lane is missing from the Makefile."
        frontier.extend(match.group(1).split())

    while frontier:
        target = frontier.pop()
        if target in reachable:
            continue
        reachable.add(target)
        expansion = re.search(rf"^{re.escape(target)}: (.+)$", makefile, re.M)
        if expansion is not None:
            frontier.extend(expansion.group(1).split())
    return reachable


def test_every_declared_gate_is_reachable_from_a_blocking_lane() -> None:
    declared = _declared_gate_targets()
    reachable = _reachable_from_lanes()

    assert reachable, (
        "No targets reachable from the blocking lanes; this check would assert nothing."
    )

    dead = sorted(name for name in declared if name not in reachable and name not in DISPOSITIONED)

    assert dead == [], (
        "These targets are declared and named as if they enforce something, but no blocking lane "
        f"invokes them, so they have never run in CI: {dead}. Wire them into `check`/`ci`, delete "
        "them if obsolete, or add them to DISPOSITIONED with the reason. See issue #182."
    )


def test_dispositioned_targets_still_exist() -> None:
    """An allowance for a target that no longer exists is stale, and hides the next one."""

    declared = _declared_gate_targets()

    missing = sorted(name for name in DISPOSITIONED if name not in declared)
    assert missing == [], (
        f"These targets are dispositioned as running outside the lanes but are no longer declared: "
        f"{missing}. Remove the allowance."
    )


def test_dispositioned_targets_are_not_also_in_the_lanes() -> None:
    """A target both wired and excused is a contradiction, and the excuse would go unnoticed."""

    reachable = _reachable_from_lanes()

    contradictory = sorted(name for name in DISPOSITIONED if name in reachable)
    assert contradictory == [], (
        f"These targets are reachable from a blocking lane AND carry a disposition saying they run "
        f"elsewhere: {contradictory}. Remove the allowance - it is no longer true."
    )
