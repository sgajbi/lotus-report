"""Evidence workflows must queue; PR-lane workflows must cancel (#180, #221, #222).

The two classes have opposite correct settings, and a guard that pins only one
of them invites "make them all the same":

- **Evidence** (main releasability, its coverage audit) is a record. A cancelled
  run reaches no verdict, so cancelling one silently deletes the only proof a
  commit was ever validated - and worse, it INVERTS the report: the coverage
  audit counts only success/failure as a verdict, so a cancelled run makes its
  commit read as *ungated*. Observed 2026-08-31: a backfill dispatch cancelled
  the just-merged commit's live gate run.
- **PR lanes** (feature lane, merge gate) are feedback on a moving branch.
  Superseded pushes SHOULD cancel; keeping them wastes runners and reports a
  verdict on a tree nobody has any more.

Parsed with ``yaml.safe_load`` so ``false`` is the boolean, not a substring a
comment could satisfy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"

EVIDENCE_WORKFLOWS = ("main-releasability.yml", "main-gate-coverage-audit.yml")
PR_LANE_WORKFLOWS = ("feature-lane.yml", "pr-merge-gate.yml")


def _concurrency(workflow: str) -> Any:
    parsed = yaml.safe_load((WORKFLOW_ROOT / workflow).read_text(encoding="utf-8"))
    return parsed.get("concurrency")


@pytest.mark.parametrize("workflow", EVIDENCE_WORKFLOWS)
def test_evidence_runs_queue_and_are_never_cancelled(workflow: str) -> None:
    concurrency = _concurrency(workflow)
    if concurrency is None:
        # No group at all also means no run can cancel another - acceptable.
        return
    assert concurrency.get("cancel-in-progress") is False, (
        f"{workflow} may cancel a releasability run. A cancelled run reaches no "
        "verdict, so it deletes the only proof its commit was validated and the "
        "coverage audit then reports that commit as ungated."
    )


def test_the_releasability_group_names_a_revision_not_a_branch() -> None:
    """A branch-keyed group puts every main commit in one group, so a later
    merge cancels an earlier commit's in-flight gate (#180's third path)."""

    group = str(_concurrency("main-releasability.yml")["group"])

    assert "github.sha" in group or "expected_sha" in group, (
        f"the releasability group must name the revision it validates: {group!r}"
    )
    assert "github.ref" not in group, (
        f"a branch-keyed group lets a later merge cancel an earlier commit's gate: {group!r}"
    )


@pytest.mark.parametrize("workflow", PR_LANE_WORKFLOWS)
def test_pr_lane_runs_still_cancel_when_superseded(workflow: str) -> None:
    """The opposite setting is correct here, and asserting it keeps the two
    classes distinguishable rather than reading as unexplained drift."""

    concurrency = _concurrency(workflow)
    # Unlike the evidence workflows, absence is NOT acceptable here: with no
    # group at all, superseded pushes queue instead of cancelling - the very
    # behaviour this guard exists to prevent, so a missing block must fail
    # rather than pass vacuously.
    assert isinstance(concurrency, dict), (
        f"{workflow} has no concurrency group, so superseded pushes queue instead of cancelling."
    )
    assert concurrency.get("cancel-in-progress") is True, (
        f"{workflow} is PR-lane feedback on a moving branch; a superseded push "
        "should cancel rather than report a verdict on a tree nobody has."
    )


def test_the_coverage_audit_is_itself_dispatchable() -> None:
    """A schedule is not a guarantee that anything runs: GitHub disables cron
    on inactivity, and a never-running audit is indistinguishable from a
    passing one. Manual dispatch is the liveness escape hatch."""

    parsed = yaml.safe_load(
        (WORKFLOW_ROOT / "main-gate-coverage-audit.yml").read_text(encoding="utf-8")
    )
    # `on:` parses as the boolean True in YAML 1.1.
    triggers = parsed.get("on", parsed.get(True))

    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers


def test_the_audit_window_is_a_time_span_not_a_commit_count() -> None:
    """A commit-count window ages commits out unexamined on a busy day: 26
    lotus-report commits and 11 lotus-render commits were found ungated only
    after widening past a count-based window."""

    workflow = (WORKFLOW_ROOT / "main-gate-coverage-audit.yml").read_text(encoding="utf-8")

    assert "--since-days" in workflow
    assert "--fail-on-gap" in workflow
