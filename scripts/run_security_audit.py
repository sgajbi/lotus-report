from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from dependency_vulnerability_exceptions import (
    pip_audit_ignore_args,
    validate_exception_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pip-audit with governed exceptions.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root. Defaults to the parent of scripts/.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate exception governance without running pip-audit.",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    exception_file = repo_root / "docs" / "standards" / "dependency-vulnerability-exceptions.json"
    requirements_file = repo_root / "requirements-audit.txt"
    exceptions = validate_exception_file(exception_file)
    if args.check_only:
        print(f"Validated {len(exceptions)} dependency vulnerability exception(s)")
        return 0

    command = [
        sys.executable,
        "-m",
        "pip_audit",
        *pip_audit_ignore_args(exceptions),
        "-r",
        str(requirements_file),
    ]
    return subprocess.run(command, cwd=repo_root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
