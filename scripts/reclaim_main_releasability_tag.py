"""Safely reclaim the immutable tag consumed by a main-releasability run."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

DISPATCH_TAG_PATTERN = re.compile(r"^main-releasability-(?P<sha>[0-9a-f]{40})$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""


class Runner(Protocol):
    def run(self, arguments: Sequence[str]) -> CommandResult: ...


class GhRunner:
    def run(self, arguments: Sequence[str]) -> CommandResult:
        completed = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
        )
        return CommandResult(completed.returncode, completed.stdout)


def _warning(message: str) -> None:
    print(f"::warning::{message}")


def reclaim_dispatch_tag(environment: Mapping[str, str], runner: Runner) -> bool:
    """Delete only the exact governed tag and return whether deletion succeeded."""
    dispatch_ref = environment.get("DISPATCH_REF", "")
    expected_sha = environment.get("EXPECTED_SHA", "")
    ref_type = environment.get("GITHUB_REF_TYPE", "")
    repository = environment.get("GITHUB_REPOSITORY", "")

    if ref_type != "tag":
        _warning(f"Ref {dispatch_ref or '<unset>'} is not a tag; retaining it.")
        return False
    match = DISPATCH_TAG_PATTERN.fullmatch(dispatch_ref)
    if match is None:
        _warning(f"Ref {dispatch_ref or '<unset>'} is not a governed dispatch tag; retaining it.")
        return False
    if not REPOSITORY_PATTERN.fullmatch(repository):
        _warning("GITHUB_REPOSITORY is absent or invalid; retaining the dispatch tag.")
        return False
    if match.group("sha") != expected_sha:
        expected_label = expected_sha or "unset"
        _warning(f"Ref {dispatch_ref} does not match expected SHA {expected_label}; retaining it.")
        return False

    lookup = runner.run(
        ["gh", "api", f"repos/{repository}/git/ref/tags/{dispatch_ref}", "--jq", ".object.sha"]
    )
    if lookup.returncode != 0:
        _warning(f"Dispatch tag {dispatch_ref} is already absent or could not be read.")
        return False
    actual_sha = lookup.stdout.strip()
    if actual_sha != expected_sha:
        _warning(
            f"Dispatch tag {dispatch_ref} points to {actual_sha}, not {expected_sha}; retaining it."
        )
        return False

    deletion = runner.run(
        ["gh", "api", "--method", "DELETE", f"repos/{repository}/git/refs/tags/{dispatch_ref}"]
    )
    if deletion.returncode != 0:
        _warning(f"Could not delete {dispatch_ref}; releasability verdict is unchanged.")
        return False
    print(f"Reclaimed consumed dispatch tag {dispatch_ref}.")
    return True


def main() -> int:
    reclaim_dispatch_tag(os.environ, GhRunner())
    return 0


if __name__ == "__main__":
    sys.exit(main())
