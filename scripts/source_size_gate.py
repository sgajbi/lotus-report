"""Reject source modules that exceed the governed maintainability limit.

A gate that inspected nothing must fail. Rename `src/`, restructure the package, or pass a
wrong `--source-root`, and an earlier version reported
`Source size gate passed: no Python source file exceeds 450 lines` for ever - true, and
meaningless, because there were no files. It was the only one of this repository's four
code-health gates that could not fail loudly; the other three shell out to tools that error
when they find nothing to do.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = ROOT / "src"
DEFAULT_MAX_LINES = int(os.getenv("SOURCE_FILE_MAX_LINES", "450"))


@dataclass(frozen=True)
class SourceSizeViolation:
    path: Path
    lines: int


def find_source_size_violations(
    source_root: Path,
    *,
    max_lines: int,
) -> tuple[list[SourceSizeViolation], int]:
    """Return the violations and how many files were inspected.

    The count is returned rather than logged so the caller can fail on zero. A gate's own report of
    what it looked at is the only thing that distinguishes "nothing is too long" from "there was
    nothing to look at".
    """

    violations: list[SourceSizeViolation] = []
    inspected = 0
    for path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        inspected += 1
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > max_lines:
            violations.append(SourceSizeViolation(path=path, lines=lines))
    return violations, inspected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    violations, inspected = find_source_size_violations(args.source_root, max_lines=args.max_lines)
    if inspected == 0:
        print(
            f"Source size gate failed: inspected 0 files under {args.source_root}. "
            "A gate that inspected nothing cannot report success.",
        )
        return 1
    if violations:
        print(f"Source size gate failed: maximum {args.max_lines} lines per Python source file.")
        for violation in violations:
            print(f"- {violation.path}: {violation.lines} lines")
        return 1
    print(
        f"Source size gate passed: {inspected} files inspected, "
        f"none exceeding {args.max_lines} lines."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
