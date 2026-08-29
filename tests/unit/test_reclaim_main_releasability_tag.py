"""Behavioral tests for destructive main-releasability tag cleanup guards."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.reclaim_main_releasability_tag import (
    CommandResult,
    reclaim_dispatch_tag,
)

SHA = "a" * 40
TAG = f"main-releasability-{SHA}"


class StubRunner:
    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[list[str]] = []

    def run(self, arguments: Sequence[str]) -> CommandResult:
        self.calls.append(list(arguments))
        return self.results.pop(0)


def _environment(**overrides: str) -> dict[str, str]:
    environment = {
        "DISPATCH_REF": TAG,
        "EXPECTED_SHA": SHA,
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REPOSITORY": "sgajbi/lotus-risk",
    }
    environment.update(overrides)
    return environment


@pytest.mark.parametrize(
    "overrides",
    [
        {"GITHUB_REF_TYPE": "branch"},
        {"DISPATCH_REF": "release-v1"},
        {"DISPATCH_REF": f"main-releasability-{'b' * 40}"},
        {"EXPECTED_SHA": ""},
        {"GITHUB_REPOSITORY": ""},
    ],
)
def test_invalid_identity_never_calls_github(overrides: dict[str, str]) -> None:
    runner = StubRunner()

    assert reclaim_dispatch_tag(_environment(**overrides), runner) is False
    assert runner.calls == []


def test_lookup_failure_never_attempts_deletion() -> None:
    runner = StubRunner([CommandResult(1)])

    assert reclaim_dispatch_tag(_environment(), runner) is False
    assert len(runner.calls) == 1
    assert "--method" not in runner.calls[0]


def test_mismatched_target_sha_never_attempts_deletion() -> None:
    runner = StubRunner([CommandResult(0, "b" * 40)])

    assert reclaim_dispatch_tag(_environment(), runner) is False
    assert len(runner.calls) == 1


def test_exact_identity_deletes_the_tag_once() -> None:
    runner = StubRunner([CommandResult(0, f"{SHA}\n"), CommandResult(0)])

    assert reclaim_dispatch_tag(_environment(), runner) is True
    assert runner.calls == [
        [
            "gh",
            "api",
            f"repos/sgajbi/lotus-risk/git/ref/tags/{TAG}",
            "--jq",
            ".object.sha",
        ],
        [
            "gh",
            "api",
            "--method",
            "DELETE",
            f"repos/sgajbi/lotus-risk/git/refs/tags/{TAG}",
        ],
    ]


def test_delete_failure_is_non_blocking_and_reported_as_not_reclaimed() -> None:
    runner = StubRunner([CommandResult(0, SHA), CommandResult(1)])

    assert reclaim_dispatch_tag(_environment(), runner) is False
    assert len(runner.calls) == 2


def test_reclaim_job_is_wired_to_run_after_the_gate_without_touching_its_verdict() -> None:
    """The end-of-run reclaim is what stops governed dispatch tags accumulating forever.

    It must never change the gate's verdict, so the job and every fallible step carry
    continue-on-error, and it fires only for governed tag dispatches.
    """
    import yaml

    workflow_path = Path(__file__).resolve().parents[2] / (
        ".github/workflows/main-releasability.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    reclaim = workflow["jobs"]["reclaim-dispatch-tag"]

    assert reclaim["continue-on-error"] is True
    assert "always()" in reclaim["if"]
    assert "startsWith(github.ref_name, 'main-releasability-')" in reclaim["if"]
    steps = {step.get("name", step.get("uses", "")): step for step in reclaim["steps"]}
    delete_step = steps["Delete consumed immutable dispatch tag"]
    assert delete_step["continue-on-error"] is True
    assert delete_step["run"] == "python scripts/reclaim_main_releasability_tag.py"
