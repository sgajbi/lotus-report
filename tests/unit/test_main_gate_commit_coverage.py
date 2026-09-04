"""Guards for per-commit main-gate coverage (found by cross-repo review, 2026-08-31).

This repository merges by rebase, so a merged PR of N commits puts N commits
on main. The dispatcher previously named only ``merge_commit_sha`` - 36 of the
45 commits merged over 28-31 Aug had no releasability run, invisibly, because
a run that is never created is not a failure. These tests pin the two halves
of the fix: the dispatcher enumerates every merged revision, and the daily
audit fails closed rather than passing while verifying nothing.
"""

from __future__ import annotations

from pathlib import Path

from scripts import audit_main_gate_coverage as audit

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"


def test_merged_pr_dispatch_gates_every_revision_the_pr_put_on_main() -> None:
    dispatcher = (WORKFLOW_ROOT / "merged-pr-main-releasability.yml").read_text(encoding="utf-8")

    # Enumeration of every revision, oldest first, from full history.
    assert "COMMIT_COUNT: ${{ github.event.pull_request.commits }}" in dispatcher
    assert 'git rev-list -n "$COMMIT_COUNT" "$MERGE_COMMIT_SHA" | tac' in dispatcher
    assert "for revision in $revisions; do" in dispatcher
    assert "fetch-depth: 0" in dispatcher
    # Every dispatch is pinned to its own revision, not the PR head.
    assert 'dispatch_ref="main-releasability-${revision}"' in dispatcher
    assert '-f expected_sha="$revision"' in dispatcher
    # The enumeration is only correct under rebase-only merging; the
    # dispatcher must fail loudly if the repository setting ever changes.
    assert "allow_squash_merge" in dispatcher
    assert '"$merge_methods" != "false,false,true"' in dispatcher
    # Ancestry is judged against the freshly fetched main, and a revision that
    # is not main history is refused BEFORE any tag is created or gate
    # dispatched: the guard must sit inside the loop, after the detach onto
    # FETCH_HEAD and ahead of both the tag write and the workflow dispatch.
    assert "git checkout --quiet --detach FETCH_HEAD" in dispatcher
    guard = 'if ! git merge-base --is-ancestor "$revision" HEAD; then'
    assert guard in dispatcher
    assert dispatcher.index("git fetch origin main --quiet") < dispatcher.index(
        "git checkout --quiet --detach FETCH_HEAD"
    )
    assert dispatcher.index("for revision in $revisions; do") < dispatcher.index(guard)
    assert dispatcher.index(guard) < dispatcher.index(
        'dispatch_ref="main-releasability-${revision}"'
    )
    assert dispatcher.index(guard) < dispatcher.index('gh api "repos/$GITHUB_REPOSITORY/git/refs"')
    assert dispatcher.index(guard) < dispatcher.index("gh workflow run main-releasability.yml")


def test_coverage_audit_workflow_runs_the_fail_closed_audit() -> None:
    workflow = (WORKFLOW_ROOT / "main-gate-coverage-audit.yml").read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "workflow_dispatch" in workflow
    assert "python scripts/audit_main_gate_coverage.py" in workflow
    assert "--fail-on-gap" in workflow


def test_audit_counts_only_verdict_bearing_runs_and_fails_closed(monkeypatch, capsys) -> None:
    """A cancelled run evaluated nothing, an unfetchable listing proves
    nothing, and both must fail the audit rather than pass it."""

    commits = {
        "a" * 40: ["success"],
        "b" * 40: ["cancelled"],
        "c" * 40: None,
        "d" * 40: [],
    }
    monkeypatch.setattr(
        audit,
        "_git",
        lambda *args: [f"{sha} {sha[:9]} subject line" for sha in commits],
    )
    monkeypatch.setattr(audit, "_run_conclusions", lambda sha: commits[sha])
    monkeypatch.setattr(audit.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        audit.argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse_namespace(limit=400, since_days=7, fail_on_gap=True),
    )

    exit_code = audit.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "UNGATED  ddddddddd" in output
    assert "1 passing, 0 with a failing verdict" in output
    assert "UNKNOWN  ccccccccc" in output
    assert "UNKNOWN  bbbbbbbbb" in output
    assert "1 with no verdict-bearing" in output


def test_a_full_window_is_not_reported_as_truncated(monkeypatch, capsys) -> None:
    """A window holding exactly --limit commits was fully examined; only a
    commit BEYOND the cap proves the span was cut short. Declaring truncation
    at equality would fail the scheduled audit for no reason."""

    shas = [f"{index:040x}" for index in range(3)]
    monkeypatch.setattr(
        audit,
        "_git",
        lambda *args: [f"{sha} {sha[:9]} subject line" for sha in shas],
    )
    monkeypatch.setattr(audit, "_run_conclusions", lambda sha: ["success"])
    monkeypatch.setattr(audit.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        audit.argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse_namespace(limit=3, since_days=7, fail_on_gap=True),
    )

    exit_code = audit.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "WINDOW TRUNCATED" not in output


def test_a_window_beyond_the_cap_fails_closed(monkeypatch, capsys) -> None:
    shas = [f"{index:040x}" for index in range(4)]
    monkeypatch.setattr(
        audit,
        "_git",
        lambda *args: [f"{sha} {sha[:9]} subject line" for sha in shas],
    )
    monkeypatch.setattr(audit, "_run_conclusions", lambda sha: ["success"])
    monkeypatch.setattr(audit.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        audit.argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse_namespace(limit=3, since_days=7, fail_on_gap=True),
    )

    exit_code = audit.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "WINDOW TRUNCATED" in output
    assert "audited 3 commit(s)" in output


def test_the_window_walks_every_commit_regardless_of_date_order(monkeypatch) -> None:
    """`--since` stops traversal at the first older commit, so a newer-dated
    ancestor behind an older-dated one is silently omitted - a green audit for
    a window it never walked. `--since-as-filter` visits every commit."""

    recorded: list[tuple[str, ...]] = []

    def _record(*args: str) -> list[str]:
        recorded.append(args)
        return []

    monkeypatch.setattr(audit, "_git", _record)
    monkeypatch.setattr(audit.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        audit.argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse_namespace(limit=400, since_days=7, fail_on_gap=True),
    )

    audit.main()

    assert recorded, "the audit never asked git for the window"
    flags = recorded[0]
    assert any(flag.startswith("--since-as-filter=") for flag in flags), flags
    assert not any(flag.startswith("--since=") for flag in flags), (
        "plain --since truncates the window at the first older commit"
    )


def test_audit_fails_closed_when_gh_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(audit.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        audit.argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse_namespace(limit=400, since_days=7, fail_on_gap=True),
    )

    assert audit.main() == 1


def test_audit_passes_when_every_commit_has_a_verdict(monkeypatch, capsys) -> None:
    """A failing verdict is information, not a coverage gap: the audit passes
    but reports the split so coverage and releasability stay distinct claims."""

    commits = {"a" * 40: ["success"], "b" * 40: ["failure", "cancelled"]}
    monkeypatch.setattr(
        audit,
        "_git",
        lambda *args: [f"{sha} {sha[:9]} subject line" for sha in commits],
    )
    monkeypatch.setattr(audit, "_run_conclusions", lambda sha: commits[sha])
    monkeypatch.setattr(audit.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        audit.argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse_namespace(limit=400, since_days=7, fail_on_gap=True),
    )

    assert audit.main() == 0
    output = capsys.readouterr().out
    assert "1 passing, 1 with a failing verdict" in output
    assert "FAILING  bbbbbbbbb" in output


def argparse_namespace(**kwargs):
    import argparse

    return argparse.Namespace(**kwargs)
