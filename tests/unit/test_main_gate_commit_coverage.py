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
    """The dispatcher is one program in the platform contract's script form (#364).

    The behavioral halves are proven elsewhere on real machinery —
    enumeration semantics on real git histories
    (test_enumerate_merged_revisions.py: base..tip bounded by the BASE, never
    commit count; dropped-duplicate; empty; contiguity; single parent;
    fetched-main ancestry) and tag/dispatch ordering with refusal-before-
    effect against a scripted gh (test_dispatch_merged_revisions.py). What
    the workflow text must pin is the BINDING: the step runs exactly the
    declared entrypoint with the inputs the program needs, so the
    conformance declaration cannot drift from what actually executes.
    """
    import json

    dispatcher = (WORKFLOW_ROOT / "merged-pr-main-releasability.yml").read_text(encoding="utf-8")
    declaration = json.loads(
        (ROOT / ".github" / "merged-revision-dispatch.conformance.json").read_text(encoding="utf-8")
    )

    # The whole dispatch step is the declared entrypoint, single line — the
    # platform recognizer classifies script form only for a single-line run,
    # and binds the declaration to this exact command.
    entrypoint = str(declaration["entrypoint"])
    assert f"run: {entrypoint}\n" in dispatcher
    program = entrypoint.split()[1]
    assert (ROOT / program).is_file()

    # The program's inputs ride the step env; missing any of them refuses.
    for variable in (
        "BASE_SHA: ${{ github.event.pull_request.base.sha }}",
        "MERGE_COMMIT_SHA: ${{ github.event.pull_request.merge_commit_sha }}",
        "COMMIT_COUNT: ${{ github.event.pull_request.commits }}",
        "PR_NUMBER: ${{ github.event.pull_request.number }}",
    ):
        assert variable in dispatcher
    # Every revision the PR added must be enumerable, not just the head.
    assert "fetch-depth: 0" in dispatcher

    # The declaration's substance: range enumeration (never commit count),
    # asymmetric cross-check, immutable-ref identity, every contract
    # semantic claimed true, and only existing test files as proofs.
    assert declaration["form"] == "script"
    assert declaration["enumeration"] == "range"
    assert declaration["count_cross_check"] == "asymmetric"
    assert declaration["tested_source_identity"] == "immutable-ref"
    assert all(value is True for value in declaration["semantics"].values())
    proofs = declaration["proofs"]
    assert proofs, "a declaration without proofs verifies nothing"
    for proof in proofs:
        assert (ROOT / proof).is_file(), f"declared proof missing: {proof}"


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
