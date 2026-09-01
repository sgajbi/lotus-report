"""Audit which commits on main the Main Releasability Gate actually evaluated.

The gate is dispatched per merged pull request; this repository merges by
rebase, so a pull request holding N commits puts N on main and every one of
them must have a gate run - a commit that was never head becomes the deployed
tree on rollback and bisect. A run that is never created is not a failure, so
nothing else reports the loss; this audit does.

Fail-closed by design (a watchdog that can pass while verifying nothing is
the same liveness defect it exists to catch):

- a missing ``gh`` binary is a failure under ``--fail-on-gap``, never a skip;
- a commit whose run listing cannot be fetched (rate limit, token scope,
  transient API failure) is UNKNOWN, and unknown commits fail the audit under
  ``--fail-on-gap`` - they are unverified, not implicitly fine;
- only runs that reached a verdict (success or failure) count as evaluation:
  a run cancelled seconds after dispatch evaluated nothing. In-progress runs
  count as pending (unknown), not as coverage.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

WORKFLOW = "main-releasability.yml"
_VERDICT_CONCLUSIONS = {"success", "failure"}


def _git(*args: str) -> list[str]:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _run_conclusions(sha: str) -> list[str] | None:
    """Conclusions of every gate run for one commit, or None when unknowable."""

    completed = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            WORKFLOW,
            "--commit",
            sha,
            "--json",
            "conclusion,status",
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    try:
        runs = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return [str(run.get("conclusion") or run.get("status") or "") for run in runs]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-days",
        type=int,
        default=7,
        help=(
            "audit every commit on origin/main from the last N days; a time window, "
            "not a commit count, so a busy day cannot age a commit out unexamined"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=400,
        help=(
            "safety cap on commits examined; reaching it means the window was "
            "truncated, which is unverified coverage and fails under --fail-on-gap"
        ),
    )
    parser.add_argument(
        "--fail-on-gap",
        action="store_true",
        help=(
            "exit non-zero when a commit has no verdict-bearing releasability run "
            "OR when any commit could not be verified (unknown fails closed)"
        ),
    )
    arguments = parser.parse_args()

    if shutil.which("gh") is None:
        print("gh is not available; cannot ask which commits the gate evaluated.")
        return 1 if arguments.fail_on_gap else 0

    # Probe one past the cap: a window holding exactly `limit` commits was fully
    # examined, so only a (limit + 1)th commit proves the span was truncated.
    probed = _git(
        "log",
        f"--since={arguments.since_days} days ago",
        f"-{arguments.limit + 1}",
        "--format=%H %h %s",
        "origin/main",
    )
    # A prefix of the window is not the window: if the cap was exceeded, older
    # commits inside the requested span went unexamined and their coverage is
    # unknown, not proven.
    truncated = len(probed) > arguments.limit
    commits = probed[: arguments.limit]
    ungated: list[str] = []
    unknown: list[str] = []
    failing: list[str] = []
    passing = 0

    for entry in commits:
        sha, short, subject = entry.split(" ", 2)
        conclusions = _run_conclusions(sha)
        if conclusions is None:
            unknown.append(short)
            print(f"UNKNOWN  {short}  (run listing could not be fetched)")
            continue
        verdicts = [conclusion for conclusion in conclusions if conclusion in _VERDICT_CONCLUSIONS]
        if verdicts:
            if "success" in verdicts:
                passing += 1
            else:
                failing.append(f"{short}  {subject[:70]}")
            continue
        if conclusions:
            # Runs exist but none reached a verdict (cancelled / in progress):
            # not proven ungated, but not verified either.
            unknown.append(short)
            print(f"UNKNOWN  {short}  (runs exist without a verdict: {sorted(set(conclusions))})")
            continue
        ungated.append(f"{short}  {subject[:70]}")
        print(f"UNGATED  {short}  {subject[:70]}")

    print(
        f"\naudited {len(commits)} commit(s) on main from the last "
        f"{arguments.since_days} day(s); "
        f"{len(ungated)} with no verdict-bearing {WORKFLOW} run; "
        f"{len(unknown)} unverifiable; "
        f"{passing} passing, {len(failing)} with a failing verdict."
    )
    if truncated:
        print(
            f"WINDOW TRUNCATED: the cap of {arguments.limit} commit(s) was reached, so "
            f"older commits inside the {arguments.since_days}-day window went unexamined. "
            "A prefix of the window is not the window; raise --limit and run again."
        )
    # Coverage is the invariant; the pass/fail split is reported beside it
    # because they are different claims: a failing verdict is information
    # (a backfilled historical tree measured against today's environment, or
    # an intermediate commit that fails its own tests), a missing run is
    # not. The audit fails only on missing/unverifiable coverage.
    for entry in failing:
        print(f"FAILING  {entry}")
    if ungated:
        print(
            "\nBackfill one with:\n"
            "  gh api repos/OWNER/REPO/git/refs "
            "-f ref=refs/tags/main-releasability-SHA -f sha=SHA\n"
            "  gh workflow run main-releasability.yml --ref main-releasability-SHA "
            "-f expected_sha=SHA -f triggering_pr=backfill\n"
        )
    if arguments.fail_on_gap and (ungated or unknown or truncated):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
