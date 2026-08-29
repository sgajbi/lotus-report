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

Issue #187 then corrected this module's boundary: reachability from `check`/`ci` measures intent,
because no workflow invokes those aggregate lanes. Enforcement is what the workflows actually run,
so the workflow-reachability test below reads `.github/workflows/*.yml` and follows `$(MAKE)`
recipe chaining - a gate is alive only when a CI lane executes it, directly or transitively.
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


def _prerequisites(header_remainder: str) -> list[str]:
    """Prerequisite names from a target header, with any inline comment discarded.

    `lint: real-dep # gate-name is checked separately` names one prerequisite; the
    comment text must not resurrect a gate as reachable.
    """
    return header_remainder.split("#", 1)[0].split()


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
        frontier.extend(_prerequisites(match.group(1)))

    while frontier:
        target = frontier.pop()
        if target in reachable:
            continue
        reachable.add(target)
        expansion = re.search(rf"^{re.escape(target)}: (.+)$", makefile, re.M)
        if expansion is not None:
            frontier.extend(_prerequisites(expansion.group(1)))
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


WORKFLOWS_DIR = ROOT / ".github" / "workflows"

# Enforcement means a lane that blocks merge or release. The feature lane and the
# auto-merge/dispatch plumbing run other things; a gate invoked only there would be
# advisory, not enforced, so discovery is restricted to the two governed lanes.
BLOCKING_WORKFLOWS = ("pr-merge-gate.yml", "main-releasability.yml")

# `make <target>` at unconditional command position: line start, or after `&&`/`;`. A command
# after `||` runs only when the previous one failed, and conditional is not enforcement - which
# also rejects the `true || make <gate>` bypass. Text that merely mentions a target (an echo
# argument, a quoted string) is not execution either.
#
# Threat model: these matchers refuse *drift* - a gate removed, commented out, quoted away, or
# conditioned off. An author deliberately constructing an evasion the shell would accept (a make
# wrapper function, an eval, a variable holding the word "make") is beyond any static matcher;
# review owns that boundary, and this comment records where it lies.
_WORKFLOW_MAKE = re.compile(r"(?:^|&&|;)\s*make\s+([a-z][a-z0-9-]*)")

_QUOTED_TEXT = re.compile(r"'[^']*'|\"[^\"]*\"")


def _strip_noncommand_text(command: str) -> str:
    """Drop quoted spans and inline comments - text the shell prints or ignores.

    `echo "manual fallback; make gate"` and `echo ok # ; make gate` must not count
    as invocations. A genuine invocation nested inside quotes (`sh -c "make gate"`)
    is also excluded by this rule; the governed lanes invoke make directly, and an
    invocation hidden in a subshell string is indistinguishable from prose here.
    """
    without_quotes = _QUOTED_TEXT.sub("", command)
    return without_quotes.split("#", 1)[0]


# `$(MAKE) <target>` at command position in a recipe line - same discipline as the
# workflow matcher: a commented-out recursive make, or one quoted inside an echo,
# is not an invocation.
_RECIPE_MAKE = re.compile(r"(?:^|&&|;)\s*\$\(MAKE\) ([a-z][a-z0-9-]*)")


def _recipe_invoked_targets(block: str) -> list[str]:
    """Targets a recipe chains to via $(MAKE), ignoring comments and quoted text."""
    invoked: list[str] = []
    for line in block.splitlines():
        if not line.startswith("	"):
            continue
        command = line.lstrip("	").lstrip()
        while command[:1] in {"@", "-", "+"}:
            command = command[1:].lstrip()
        if command.startswith("#"):
            continue
        invoked.extend(_RECIPE_MAKE.findall(_strip_noncommand_text(command)))
    return invoked


def _workflow_run_commands(workflow_path: Path) -> list[str]:
    """The executable `run` values of every step, and nothing else.

    Scanning raw YAML would count `make <gate>` inside comments, step names, or a
    commented-out `run:` line as CI execution - exactly the drift this test exists
    to refuse.
    """
    import yaml

    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    commands: list[str] = []
    for job in (workflow.get("jobs") or {}).values():
        if "if" in job:
            # A conditioned job is conditional execution, and conditional is not enforced.
            continue
        for step in job.get("steps") or []:
            if "if" in step:
                continue
            run = step.get("run")
            if isinstance(run, str):
                commands.append(run)
    return commands


def _workflow_invoked_targets(workflow_path: Path) -> set[str]:
    invoked: set[str] = set()
    for command in _workflow_run_commands(workflow_path):
        for line in command.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                # A shell-commented line inside a live run block never executes.
                continue
            invoked.update(_WORKFLOW_MAKE.findall(_strip_noncommand_text(stripped)))
    return invoked


def _makefile_blocks() -> dict[str, str]:
    """Each target's header-plus-recipe block, so recipe-level $(MAKE) chaining is visible."""

    makefile = _makefile()
    blocks: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in makefile.splitlines(keepends=True):
        header = re.match(r"^([a-z][a-z0-9-]*):", line)
        if header is not None:
            if current is not None:
                blocks[current] = "".join(lines)
            current = header.group(1)
            lines = [line]
        elif current is not None:
            lines.append(line)
    if current is not None:
        blocks[current] = "".join(lines)
    return blocks


def _workflow_reachable_targets(workflow_path: Path) -> set[str]:
    """Targets one workflow executes: directly, via prerequisites, or via $(MAKE) recipes."""

    blocks = _makefile_blocks()
    reachable: set[str] = set()
    frontier = list(_workflow_invoked_targets(workflow_path))
    while frontier:
        target = frontier.pop()
        if target in reachable:
            continue
        reachable.add(target)
        block = blocks.get(target)
        if block is None:
            continue
        header = re.match(rf"^{re.escape(target)}: (.+)$", block, re.M)
        if header is not None:
            frontier.extend(_prerequisites(header.group(1)))
        frontier.extend(_recipe_invoked_targets(block))
    return reachable


def test_every_declared_gate_is_executed_by_some_workflow() -> None:
    """Lane reachability is intent; this is enforcement.

    No workflow invokes `make check` or `make ci`, so a gate reachable only from those lanes has
    never run in CI (issue #187). Every gate-shaped target must be executed by at least one
    workflow - directly, or transitively through a target a workflow invokes (the
    `lint` -> `$(MAKE) monetary-float-guard` recipe path counts, and must, or the one gate that
    does work would be a false positive here).
    """

    declared = _declared_gate_targets()

    for workflow_name in BLOCKING_WORKFLOWS:
        workflow_path = WORKFLOWS_DIR / workflow_name
        assert workflow_path.exists(), (
            f"Blocking workflow {workflow_name} is missing. If a governed lane was renamed, "
            "update BLOCKING_WORKFLOWS - with the old name gone this check asserts nothing."
        )
        reachable = _workflow_reachable_targets(workflow_path)
        assert reachable, (
            f"{workflow_name} invokes no make targets; this check would assert nothing."
        )
        dead = sorted(name for name in declared if name not in reachable)
        assert dead == [], (
            f"These gate targets are declared in the Makefile, but {workflow_name} does not "
            f"execute them directly or transitively: {dead}. Both governed lanes must run every "
            "gate - one lane alone allows either unvalidated merges or an unvalidated exact "
            "merged revision. See #187."
        )
