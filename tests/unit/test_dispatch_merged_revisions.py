"""Behavior tests for the dispatch half of the merged-revision dispatcher.

Enumeration semantics are proven on real git histories in
`test_enumerate_merged_revisions.py`; these prove what happens AFTER a
correct enumeration — merge-methods refusal, immutable-ref tag handling,
dispatch arguments, ordering, and that every refusal happens before any
effect — against a scripted fake `gh`, which records every invocation the
way the platform contract's audit reads run evidence.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.dispatch_merged_revisions import (
    DispatchRefused,
    assert_rebase_only_merging,
    dispatch_merged_revisions,
    ensure_dispatch_ref,
)


class _FakeGh:
    """Scripted `gh`: canned answers per command shape, every call recorded."""

    def __init__(
        self,
        *,
        merge_methods: str = "false,false,true",
        existing_tags: dict[str, str] | None = None,
        failing: str | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._merge_methods = merge_methods
        self._existing_tags = dict(existing_tags or {})
        self._failing = failing

    def __call__(self, args: Sequence[str]) -> tuple[int, str]:
        call = list(args)
        self.calls.append(call)
        joined = " ".join(call)
        if self._failing and self._failing in joined:
            return 1, ""
        if call[:2] == ["api", "repos/sgajbi/lotus-report"] and "--jq" in call:
            return 0, self._merge_methods
        if call[0] == "api" and "/git/ref/tags/" in call[1]:
            ref = call[1].rsplit("/", 1)[-1]
            if ref in self._existing_tags:
                return 0, self._existing_tags[ref]
            return 1, ""
        if call[0] == "api" and call[1].endswith("/git/refs"):
            return 0, ""
        if call[:2] == ["workflow", "run"]:
            return 0, ""
        raise AssertionError(f"unscripted gh call: {call}")


def _arg(call: list[str], prefix: str) -> str:
    """The value of the one argument in `call` starting with `prefix`."""
    values = [arg[len(prefix) :] for arg in call if arg.startswith(prefix)]
    assert len(values) == 1, (prefix, call)
    return values[0]


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
    """A real origin/clone pair, because the dispatcher fetches origin main."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("config", "user.email", "test@test", cwd=origin)
    _git("config", "user.name", "test", cwd=origin)
    return origin


def _clone(origin: Path) -> Path:
    clone = origin.parent / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        capture_output=True,
        text=True,
        check=True,
    )
    return clone


def test_happy_path_tags_then_dispatches_every_revision_oldest_first(repo: Path) -> None:
    base = _commit(repo, "base")
    first = _commit(repo, "c1")
    second = _commit(repo, "c2")
    clone = _clone(repo)
    gh = _FakeGh()
    log: list[str] = []

    dispatched = dispatch_merged_revisions(
        repository="sgajbi/lotus-report",
        base_sha=base,
        merge_commit_sha=second,
        stated_commit_count=2,
        pr_number="999",
        cwd=str(clone),
        gh=gh,
        log=log.append,
    )

    assert dispatched == [first, second]
    tag_creates = [call for call in gh.calls if call[0] == "api" and call[1].endswith("/git/refs")]
    runs = [call for call in gh.calls if call[:2] == ["workflow", "run"]]
    assert [_arg(call, "sha=") for call in tag_creates] == [first, second]
    assert [_arg(call, "expected_sha=") for call in runs] == [first, second]
    # Interleaved per revision: tag N, dispatch N, tag N+1, dispatch N+1 —
    # oldest first, so verdicts land in history order.
    effect_order = [
        ("tag" if call[1].endswith("/git/refs") else "run", call)
        for call in gh.calls
        if (call[0] == "api" and call[1].endswith("/git/refs")) or call[:2] == ["workflow", "run"]
    ]
    assert [kind for kind, _ in effect_order] == ["tag", "run", "tag", "run"]
    for call in runs:
        assert call[2] == "main-releasability.yml"
        assert "--ref" in call and call[call.index("--ref") + 1].startswith("main-releasability-")
        assert "-f" in call and "triggering_pr=999" in call
        assert "source_branch=main" in call


def test_changed_merge_methods_refuse_before_any_effect(repo: Path) -> None:
    """A squash-enabled repository makes the enumeration itself a lie."""
    base = _commit(repo, "base")
    tip = _commit(repo, "c1")
    clone = _clone(repo)
    gh = _FakeGh(merge_methods="true,false,true")

    with pytest.raises(DispatchRefused, match="merge_methods_changed"):
        dispatch_merged_revisions(
            repository="sgajbi/lotus-report",
            base_sha=base,
            merge_commit_sha=tip,
            stated_commit_count=1,
            pr_number="999",
            cwd=str(clone),
            gh=gh,
            log=lambda _: None,
        )

    assert all(
        not (call[0] == "api" and call[1].endswith("/git/refs")) and call[:2] != ["workflow", "run"]
        for call in gh.calls
    ), "a refusal must precede every tag and dispatch effect"


def test_existing_tag_with_matching_sha_is_reused_not_recreated(repo: Path) -> None:
    base = _commit(repo, "base")
    tip = _commit(repo, "c1")
    clone = _clone(repo)
    gh = _FakeGh(existing_tags={f"main-releasability-{tip}": tip})

    dispatch_merged_revisions(
        repository="sgajbi/lotus-report",
        base_sha=base,
        merge_commit_sha=tip,
        stated_commit_count=1,
        pr_number="7",
        cwd=str(clone),
        gh=gh,
        log=lambda _: None,
    )

    assert not [call for call in gh.calls if call[0] == "api" and call[1].endswith("/git/refs")], (
        "an existing matching tag must be adopted, not recreated"
    )
    assert [call for call in gh.calls if call[:2] == ["workflow", "run"]], (
        "the gate must still be dispatched on the existing ref"
    )


def test_existing_tag_with_conflicting_sha_refuses_without_dispatch() -> None:
    gh = _FakeGh(existing_tags={"main-releasability-aaaa": "bbbb"})

    with pytest.raises(DispatchRefused, match="dispatch_ref_conflict"):
        ensure_dispatch_ref(repository="sgajbi/lotus-report", revision="aaaa", gh=gh)

    assert not [call for call in gh.calls if call[:2] == ["workflow", "run"]]


def test_unreadable_merge_methods_refuse() -> None:
    gh = _FakeGh(failing="repos/sgajbi/lotus-report")

    with pytest.raises(DispatchRefused, match="merge_methods_unreadable"):
        assert_rebase_only_merging(repository="sgajbi/lotus-report", gh=gh)


def test_failed_dispatch_stops_the_sequence(repo: Path) -> None:
    """A failed dispatch is a loud stop, not a skipped revision.

    The next startup of the coverage audit sees the missing verdict either
    way; what must not happen is dispatching newer revisions above a hole.
    """
    base = _commit(repo, "base")
    first = _commit(repo, "c1")
    second = _commit(repo, "c2")
    clone = _clone(repo)
    gh = _FakeGh(failing="workflow run")

    with pytest.raises(DispatchRefused, match="gate_dispatch_failed"):
        dispatch_merged_revisions(
            repository="sgajbi/lotus-report",
            base_sha=base,
            merge_commit_sha=second,
            stated_commit_count=2,
            pr_number="1",
            cwd=str(clone),
            gh=gh,
            log=lambda _: None,
        )

    tag_creates = [call for call in gh.calls if call[0] == "api" and call[1].endswith("/git/refs")]
    assert [_arg(call, "sha=") for call in tag_creates] == [first], (
        "the second revision must not be tagged above a failed first dispatch"
    )
