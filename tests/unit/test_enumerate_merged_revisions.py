"""Behavior tests for the landed-interval enumerator, on real git histories.

Each case builds an actual repository (the platform #860 pattern: behavior
from real temporary git histories, not model-only proof) and calls the same
function the workflow runs. The dropped-duplicate case is the defect #364
exists for: count enumeration walks past the PR; interval enumeration must
not.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.enumerate_merged_revisions import EnumerationRefused, enumerate_merged_revisions


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(cwd: Path, name: str) -> str:
    (cwd / name).write_text(name, encoding="utf-8")
    _git("add", "-A", cwd=cwd)
    _git("commit", "-q", "-m", name, cwd=cwd)
    return _git("rev-parse", "HEAD", cwd=cwd)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    _git("config", "user.email", "test@test", cwd=tmp_path)
    _git("config", "user.name", "test", cwd=tmp_path)
    return tmp_path


def test_enumerates_exactly_the_landed_interval_in_order(repo: Path) -> None:
    base = _commit(repo, "base")
    first = _commit(repo, "c1")
    second = _commit(repo, "c2")
    tip = _git("rev-parse", "main", cwd=repo)

    revisions = enumerate_merged_revisions(
        base_sha=base,
        merge_commit_sha=second,
        stated_commit_count=2,
        main_tip=tip,
        cwd=str(repo),
    )
    assert revisions == [first, second]


def test_dropped_duplicate_rebase_succeeds_with_notice_not_overreach(repo: Path) -> None:
    """THE defect case: the PR stated 3 commits, the rebase dropped one.

    Count enumeration (`rev-list -n 3 tip`) would reach past the PR into
    `base` — earlier landed history. Interval enumeration returns exactly the
    two landed revisions and records a notice.
    """
    _commit(repo, "earlier-pr")
    base = _commit(repo, "base")
    first = _commit(repo, "c1")
    second = _commit(repo, "c2")
    tip = _git("rev-parse", "main", cwd=repo)

    notices: list[str] = []
    revisions = enumerate_merged_revisions(
        base_sha=base,
        merge_commit_sha=second,
        stated_commit_count=3,
        main_tip=tip,
        cwd=str(repo),
        notices=notices,
    )
    assert revisions == [first, second]
    assert len(notices) == 1 and "dropped 1 duplicate" in notices[0]


def test_interval_exceeding_stated_commits_refuses_before_any_output(repo: Path) -> None:
    """More landed than stated means the interval caught someone else's work."""
    base = _commit(repo, "base")
    _commit(repo, "someone-elses")
    mine = _commit(repo, "mine")
    tip = _git("rev-parse", "main", cwd=repo)

    with pytest.raises(EnumerationRefused, match="interval_exceeds_pr_commits"):
        enumerate_merged_revisions(
            base_sha=base,
            merge_commit_sha=mine,
            stated_commit_count=1,
            main_tip=tip,
            cwd=str(repo),
        )


def test_empty_interval_refuses(repo: Path) -> None:
    base = _commit(repo, "base")
    with pytest.raises(EnumerationRefused, match="empty_interval"):
        enumerate_merged_revisions(
            base_sha=base,
            merge_commit_sha=base,
            stated_commit_count=1,
            main_tip=base,
            cwd=str(repo),
        )


def test_merge_topology_refuses(repo: Path) -> None:
    """A merged side branch cannot pass: its parallel chains break contiguity
    (or, if the merge commit is reached first, the single-parent guard). Either
    diagnostic is a correct fail-closed refusal of a non-rebase merge."""
    base = _commit(repo, "base")
    _git("checkout", "-q", "-b", "side", cwd=repo)
    _commit(repo, "side-work")
    _git("checkout", "-q", "main", cwd=repo)
    _commit(repo, "mainline")
    _git("merge", "-q", "--no-ff", "-m", "merge side", "side", cwd=repo)
    tip = _git("rev-parse", "main", cwd=repo)

    with pytest.raises(EnumerationRefused, match="not_single_parent|not_contiguous"):
        enumerate_merged_revisions(
            base_sha=base,
            merge_commit_sha=tip,
            stated_commit_count=3,
            main_tip=tip,
            cwd=str(repo),
        )


def test_lone_merge_commit_refuses_on_single_parent_guard(repo: Path) -> None:
    """The precise single-parent case: a --no-ff merge of an ancestor creates a
    merge commit whose second parent is already base history, so the interval
    is that merge commit ALONE — contiguity holds and only the parent-count
    guard can refuse it."""
    anchor = _commit(repo, "anchor")
    base = _commit(repo, "base")
    # `git merge --no-ff <ancestor>` is "already up to date" and creates
    # nothing, so build the redundant merge commit with plumbing: both parents
    # are base history, making the interval exactly this one merge commit.
    tree = _git("rev-parse", "HEAD^{tree}", cwd=repo)
    tip = _git("commit-tree", tree, "-p", base, "-p", anchor, "-m", "redundant merge", cwd=repo)
    _git("update-ref", "refs/heads/main", tip, cwd=repo)

    with pytest.raises(EnumerationRefused, match="not_single_parent"):
        enumerate_merged_revisions(
            base_sha=base,
            merge_commit_sha=tip,
            stated_commit_count=1,
            main_tip=tip,
            cwd=str(repo),
        )


def test_revision_off_the_fetched_main_refuses(repo: Path) -> None:
    """A forced move of main between merge event and dispatch must never gate."""
    base = _commit(repo, "base")
    _git("checkout", "-q", "-b", "orphan", cwd=repo)
    stray = _commit(repo, "stray")
    _git("checkout", "-q", "main", cwd=repo)
    moved_tip = _commit(repo, "moved-main")

    with pytest.raises(EnumerationRefused, match="not_on_main"):
        enumerate_merged_revisions(
            base_sha=base,
            merge_commit_sha=stray,
            stated_commit_count=1,
            main_tip=moved_tip,
            cwd=str(repo),
        )


def test_missing_base_sha_refuses(repo: Path) -> None:
    tip = _commit(repo, "base")
    with pytest.raises(EnumerationRefused, match="base_sha_required"):
        enumerate_merged_revisions(
            base_sha="",
            merge_commit_sha=tip,
            stated_commit_count=1,
            main_tip=tip,
            cwd=str(repo),
        )
