"""Verify the actual hosted Docker image and optional service container identity.

Docker/GitHub service initialization owns acquisition and fails before this check
on unavailable or unauthorized pulls. This check never pulls, retries or falls back.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any


def _read(command: list[str], runner: Callable[..., Any]) -> Any:
    result = runner(command, capture_output=True, text=True, timeout=20, check=False)
    # Retain exact metadata/error output before checking status or interpreting JSON.
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        raise ValueError("Docker acquisition metadata unavailable")
    return json.loads(result.stdout)


def verify(
    reference: str, container_id: str | None, runner: Callable[..., Any] = subprocess.run
) -> None:
    if (
        re.fullmatch(
            r"public\.ecr\.aws/docker/library/(python|postgres)@sha256:[0-9a-f]{64}", reference
        )
        is None
    ):
        raise ValueError("Reviewed immutable distribution reference required")
    image = _read(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}},'
            '"Os":{{json .Os}},"Architecture":{{json .Architecture}}}',
            reference,
        ],
        runner,
    )
    if not isinstance(image, dict):
        raise ValueError("Acquisition metadata must be an image object")
    if reference not in (image.get("RepoDigests") or []):
        raise ValueError("Acquired image does not carry the requested digest")
    if image.get("Os") != "linux" or image.get("Architecture") != "amd64":
        raise ValueError("Hosted runner platform mismatch")
    if not isinstance(image.get("Id"), str) or not image["Id"].startswith("sha256:"):
        raise ValueError("Acquired image identity missing")
    if container_id:
        actual_image = _read(
            ["docker", "container", "inspect", "--format", "{{json .Image}}", container_id], runner
        )
        if actual_image != image["Id"]:
            raise ValueError("Service container does not use the verified acquired image")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--container-id")
    args = parser.parse_args()
    try:
        verify(args.reference, args.container_id)
    except (ValueError, KeyError, TypeError, subprocess.SubprocessError, OSError) as exc:
        print(f"Image acquisition verification failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("Actual hosted image acquisition identity verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
