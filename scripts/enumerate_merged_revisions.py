"""Enumerate exactly the revisions a merged PR landed on main (report#364).

The governed landed-interval semantics (report#364 comment 5651248816):

- the interval is BOUNDED BY THE BASE, never by commit count: `rev-list
  BASE_SHA..MERGE_COMMIT_SHA`. Count enumeration (`rev-list -n COMMIT_COUNT`)
  walks past the PR into earlier merges whenever a rebase drops a duplicate
  commit, which is the defect this file replaces;
- an empty interval refuses — a merged PR that landed nothing is an event
  contradiction, not a no-op;
- asymmetric count cross-check against the PR's stated commit count:
  MORE enumerated than stated means the interval caught someone else's
  commits and refuses BEFORE any tag or dispatch; FEWER is legitimate (the
  rebase dropped duplicates) and is only a notice;
- per-revision guards: each revision must be an ancestor of freshly fetched
  main, must NOT be inside the base's history, must have exactly one parent
  (rebase-only merging admits no merge commits), and must be contiguous with
  the previous revision, the chain starting at BASE_SHA and ending exactly at
  MERGE_COMMIT_SHA.

Run inside a checkout with full history (`fetch-depth: 0`). Emits the ordered
(oldest-first) revision list as a JSON array on stdout; every refusal exits 1
with a stable `report_dispatch_enumeration_refused:` diagnostic on stderr.
Pure stdlib so the behavior tests can build real temporary git histories and
execute this exact file (`tests/unit/test_enumerate_merged_revisions.py`).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


class EnumerationRefused(RuntimeError):
    pass


def _git(*args: str, cwd: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise EnumerationRefused(
            f"report_dispatch_enumeration_refused:git_failed:{' '.join(args)}:"
            f"{result.stderr.strip()[:200]}"
        )
    return result.stdout.strip()


def _is_ancestor(candidate: str, of: str, *, cwd: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", candidate, of],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise EnumerationRefused(
        f"report_dispatch_enumeration_refused:ancestry_check_failed:{candidate}"
    )


def enumerate_merged_revisions(
    *,
    base_sha: str,
    merge_commit_sha: str,
    stated_commit_count: int,
    main_tip: str,
    cwd: str,
    notices: list[str] | None = None,
) -> list[str]:
    if not base_sha:
        raise EnumerationRefused("report_dispatch_enumeration_refused:base_sha_required")
    if not merge_commit_sha:
        raise EnumerationRefused("report_dispatch_enumeration_refused:merge_commit_sha_required")

    interval = _git("rev-list", "--reverse", f"{base_sha}..{merge_commit_sha}", cwd=cwd)
    revisions = [line for line in interval.splitlines() if line]
    if not revisions:
        raise EnumerationRefused(
            f"report_dispatch_enumeration_refused:empty_interval:{base_sha}..{merge_commit_sha}"
        )

    # Asymmetric on purpose: too many is someone else's history, too few is a
    # dropped duplicate — only the first is a defect.
    if len(revisions) > stated_commit_count:
        raise EnumerationRefused(
            "report_dispatch_enumeration_refused:interval_exceeds_pr_commits:"
            f"enumerated={len(revisions)}:stated={stated_commit_count}"
        )
    if len(revisions) < stated_commit_count and notices is not None:
        notices.append(
            f"rebase dropped {stated_commit_count - len(revisions)} duplicate "
            f"commit(s); enumerated={len(revisions)} stated={stated_commit_count}"
        )

    previous = base_sha
    for revision in revisions:
        if not _is_ancestor(revision, main_tip, cwd=cwd):
            raise EnumerationRefused(f"report_dispatch_enumeration_refused:not_on_main:{revision}")
        if _is_ancestor(revision, base_sha, cwd=cwd):
            raise EnumerationRefused(
                f"report_dispatch_enumeration_refused:inside_base_history:{revision}"
            )
        parents = _git("rev-list", "--parents", "-n", "1", revision, cwd=cwd).split()
        if len(parents) != 2:
            raise EnumerationRefused(
                "report_dispatch_enumeration_refused:not_single_parent:"
                f"{revision}:parents={len(parents) - 1}"
            )
        if parents[1] != previous:
            raise EnumerationRefused(
                "report_dispatch_enumeration_refused:not_contiguous:"
                f"{revision}:parent={parents[1]}:expected={previous}"
            )
        previous = revision

    if previous != merge_commit_sha:
        raise EnumerationRefused(
            "report_dispatch_enumeration_refused:interval_tip_mismatch:"
            f"tip={previous}:merge_commit_sha={merge_commit_sha}"
        )
    return revisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--merge-commit-sha", required=True)
    parser.add_argument("--stated-commit-count", type=int, required=True)
    parser.add_argument(
        "--main-tip",
        required=True,
        help="freshly fetched main tip (FETCH_HEAD), the ancestry authority",
    )
    parser.add_argument("--cwd", default=".")
    arguments = parser.parse_args()
    notices: list[str] = []
    try:
        revisions = enumerate_merged_revisions(
            base_sha=arguments.base_sha,
            merge_commit_sha=arguments.merge_commit_sha,
            stated_commit_count=arguments.stated_commit_count,
            main_tip=arguments.main_tip,
            cwd=arguments.cwd,
            notices=notices,
        )
    except EnumerationRefused as refusal:
        print(refusal, file=sys.stderr)
        return 1
    for notice in notices:
        print(f"::notice::{notice}", file=sys.stderr)
    print(json.dumps(revisions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
