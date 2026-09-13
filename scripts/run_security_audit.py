from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    # `python scripts/run_security_audit.py` starts with scripts/ on sys.path,
    # while test/package execution starts with the repository root. Use the
    # package import in both cases so dependency ownership stays unambiguous.
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check_dependency_constraints import (  # noqa: E402
    compare_constraints,
    installed_distributions,
    parse_constraints,
    project_dependency_names,
)
from scripts.dependency_vulnerability_exceptions import (  # noqa: E402
    pip_audit_ignore_args,
    validate_exception_file,
)


def _audit_closure_file(repo_root: Path) -> Path:
    """Return the exact dependency closure that pip-audit must inspect.

    The project's dependency floors are compatibility declarations, not the
    resolution CI actually installs.  Auditing those floors lets pip-audit
    resolve a newer package than the vulnerable package committed in the
    closure, which turns a clean audit into evidence about a different build.
    Validate exact pins, declared build/direct requirements and the ``dev``
    extra that the audit installs, then compare the closure with the resolved project
    environment before handing it to pip-audit.  The resolved comparison is
    what catches extra-derived transitive packages such as ``httptools`` from
    ``uvicorn[standard]``: they must be both installed and recorded, never
    silently resolved afresh by the scanner.
    """

    closure = repo_root / "constraints.txt"
    try:
        pins = parse_constraints(closure.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("dependency_audit_constraints_missing") from error
    if not pins:
        raise ValueError("dependency_audit_constraints_empty")

    project_name, declared_dependencies = project_dependency_names(repo_root, extras=("dev",))
    missing_declared_pins = sorted(declared_dependencies - set(pins))
    if missing_declared_pins:
        raise ValueError(
            "dependency_audit_constraints_incomplete:missing_project_pins:"
            + ",".join(missing_declared_pins)
        )

    closure_drift = compare_constraints(
        pins,
        installed_distributions(),
        project_name=project_name,
        included_tooling=declared_dependencies,
    )
    if closure_drift:
        raise ValueError(
            "dependency_audit_constraints_incomplete:resolved_environment_drift:"
            + ",".join(closure_drift[:10])
        )
    return closure


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pip-audit with governed exceptions.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
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
    exceptions = validate_exception_file(exception_file)
    if args.check_only:
        print(f"Validated {len(exceptions)} dependency vulnerability exception(s)")
        return 0

    try:
        closure = _audit_closure_file(repo_root)
    except ValueError as error:
        print(f"Security audit refused: {error}", file=sys.stderr)
        return 1

    command = [
        sys.executable,
        "-m",
        "pip_audit",
        *pip_audit_ignore_args(exceptions),
        "-r",
        str(closure),
    ]
    return subprocess.run(command, cwd=repo_root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
