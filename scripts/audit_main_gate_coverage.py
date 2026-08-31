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
        "--limit",
        type=int,
        default=60,
        help="how many commits of origin/main history to audit",
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

    commits = _git("log", f"-{arguments.limit}", "--format=%H %h %s", "origin/main")
    ungated: list[str] = []
    unknown: list[str] = []

    for entry in commits:
        sha, short, subject = entry.split(" ", 2)
        conclusions = _run_conclusions(sha)
        if conclusions is None:
            unknown.append(short)
            print(f"UNKNOWN  {short}  (run listing could not be fetched)")
            continue
        verdicts = [conclusion for conclusion in conclusions if conclusion in _VERDICT_CONCLUSIONS]
        if verdicts:
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
        f"\naudited {len(commits)} commit(s) on main; "
        f"{len(ungated)} with no verdict-bearing {WORKFLOW} run; "
        f"{len(unknown)} unverifiable."
    )
    if ungated:
        print(
            "\nBackfill one with:\n"
            "  gh api repos/OWNER/REPO/git/refs "
            "-f ref=refs/tags/main-releasability-SHA -f sha=SHA\n"
            "  gh workflow run main-releasability.yml --ref main-releasability-SHA "
            "-f expected_sha=SHA -f triggering_pr=backfill\n"
        )
    if arguments.fail_on_gap and (ungated or unknown):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
