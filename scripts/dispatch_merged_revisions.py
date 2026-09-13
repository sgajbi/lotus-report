"""Dispatch the Main Releasability gate for every revision a merged PR landed.

The whole dispatch step, as one program (report#364), conforming to the
platform's merged-revision dispatch contract
(lotus-platform platform-contracts/ci-governance/
merged-revision-dispatch-conformance.v1.json, adopted at platform main
7c5a3293) in its script form: the workflow invokes exactly
`python scripts/dispatch_merged_revisions.py`, and
`.github/merged-revision-dispatch.conformance.json` declares the semantics
with this file's tests as the named proofs.

Semantics owned here, in contract vocabulary:

- rebase-only-merge-assertion: the repository's merge settings are read
  first and anything but rebase-only refuses — a squash lands one commit
  however many the PR held, and a merge commit adds a parent the walk would
  descend into;
- range-enumeration / empty-enumeration-refusal / count-cross-check /
  main-ancestry-guard: `enumerate_merged_revisions` (its own behavior tests
  build real git histories) — base..tip bounded by pull_request.base.sha,
  never by commit count; asymmetric cross-check (more than stated refuses
  before any tag or dispatch, fewer is a notice); every revision an ancestor
  of the freshly fetched main, single-parent, contiguous base->tip;
- tested-source-identity (immutable-ref): a `main-releasability-<revision>`
  tag is looked up, refused on conflict, created when absent, and the gate is
  dispatched ON that ref, so workflow definition and tested source are the
  same revision;
- exact-revision-dispatch: every dispatch passes expected_sha equal to the
  revision, which the gate asserts against its checkout.

Dispatches are sequential, oldest first: parallel tag writes race (estate
evidence), and verdicts should land in history order. All GitHub effects go
through one injected runner so the tests exercise refusal ordering and
dispatch arguments with a fake, while production uses `gh` untouched.

merged-main-only-trigger lives in the workflow's `if:` (the event payload is
not visible here); the declaration claims it on the workflow's behalf and the
platform recognizer reads that condition from the YAML directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

# One import spelling for both real contexts. The workflow runs
# `python scripts/<file>.py`, which puts scripts/ (not the repo root) on
# sys.path; the tests import `scripts.<module>` from the repo root. Anchoring
# the repo root here keeps the package import valid in both without a bare
# fallback import that dependency hygiene would read as a third-party name.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.enumerate_merged_revisions import (  # noqa: E402
    EnumerationRefused,
    _git,
    enumerate_merged_revisions,
)

#: Runs one `gh` invocation; returns (exit_code, stdout). Injected in tests.
GhRunner = Callable[[Sequence[str]], tuple[int, str]]


class DispatchRefused(RuntimeError):
    pass


def _run_gh(args: Sequence[str]) -> tuple[int, str]:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return result.returncode, result.stdout.strip()


def assert_rebase_only_merging(*, repository: str, gh: GhRunner) -> None:
    code, output = gh(
        [
            "api",
            f"repos/{repository}",
            "--jq",
            "[.allow_squash_merge, .allow_merge_commit, .allow_rebase_merge] | @csv",
        ]
    )
    if code != 0:
        raise DispatchRefused("report_dispatch_refused:merge_methods_unreadable")
    if output.strip() != "false,false,true":
        raise DispatchRefused(
            f"report_dispatch_refused:merge_methods_changed:{output.strip()}:"
            "per-revision enumeration assumes rebase-only merging; update the "
            "dispatcher before merging anything"
        )


def ensure_dispatch_ref(*, repository: str, revision: str, gh: GhRunner) -> None:
    """The immutable-ref half of tested-source identity.

    An existing tag naming a DIFFERENT sha is refused, never repointed: the
    tag is the durable claim about which tree the verdict describes.
    """
    ref = f"main-releasability-{revision}"
    code, existing = gh(["api", f"repos/{repository}/git/ref/tags/{ref}", "--jq", ".object.sha"])
    if code == 0:
        if existing.strip() != revision:
            raise DispatchRefused(
                f"report_dispatch_refused:dispatch_ref_conflict:{ref}:"
                f"points_to={existing.strip()}:expected={revision}"
            )
        return
    code, _ = gh(
        [
            "api",
            f"repos/{repository}/git/refs",
            "-f",
            f"ref=refs/tags/{ref}",
            "-f",
            f"sha={revision}",
        ]
    )
    if code != 0:
        raise DispatchRefused(f"report_dispatch_refused:dispatch_ref_create_failed:{ref}")


def dispatch_gate(*, repository: str, revision: str, pr_number: str, gh: GhRunner) -> None:
    code, _ = gh(
        [
            "workflow",
            "run",
            "main-releasability.yml",
            "--repo",
            repository,
            "--ref",
            f"main-releasability-{revision}",
            "-f",
            f"expected_sha={revision}",
            "-f",
            f"triggering_pr={pr_number}",
            "-f",
            "source_branch=main",
        ]
    )
    if code != 0:
        raise DispatchRefused(f"report_dispatch_refused:gate_dispatch_failed:{revision}")


def dispatch_merged_revisions(
    *,
    repository: str,
    base_sha: str,
    merge_commit_sha: str,
    stated_commit_count: int,
    pr_number: str,
    cwd: str,
    gh: GhRunner,
    log: Callable[[str], None],
) -> list[str]:
    assert_rebase_only_merging(repository=repository, gh=gh)

    _git("fetch", "origin", "main", "--quiet", cwd=cwd)
    main_tip = _git("rev-parse", "FETCH_HEAD", cwd=cwd)

    notices: list[str] = []
    revisions = enumerate_merged_revisions(
        base_sha=base_sha,
        merge_commit_sha=merge_commit_sha,
        stated_commit_count=stated_commit_count,
        main_tip=main_tip,
        cwd=cwd,
        notices=notices,
    )
    for notice in notices:
        log(f"::notice::{notice}")

    for revision in revisions:
        ensure_dispatch_ref(repository=repository, revision=revision, gh=gh)
        dispatch_gate(repository=repository, revision=revision, pr_number=pr_number, gh=gh)
        log(f"Dispatched main releasability for {revision} (PR #{pr_number})")
    return revisions


def main() -> int:
    try:
        dispatch_merged_revisions(
            repository=os.environ["GITHUB_REPOSITORY"],
            base_sha=os.environ["BASE_SHA"],
            merge_commit_sha=os.environ["MERGE_COMMIT_SHA"],
            stated_commit_count=int(os.environ["COMMIT_COUNT"]),
            pr_number=os.environ["PR_NUMBER"],
            cwd=".",
            gh=_run_gh,
            log=print,
        )
    except (DispatchRefused, EnumerationRefused) as refusal:
        print(refusal, file=sys.stderr)
        return 1
    except KeyError as missing:
        print(
            f"report_dispatch_refused:environment_missing:{missing}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
