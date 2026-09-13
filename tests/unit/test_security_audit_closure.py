"""The security audit must inspect the dependency closure CI actually installs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_security_audit as audit

ROOT = Path(__file__).resolve().parents[2]


def _governed_repo(tmp_path: Path, closure: str) -> Path:
    exception_file = tmp_path / "docs" / "standards" / "dependency-vulnerability-exceptions.json"
    exception_file.parent.mkdir(parents=True)
    exception_file.write_text(json.dumps({"exceptions": []}), encoding="utf-8")
    (tmp_path / "constraints.txt").write_text(closure, encoding="utf-8")
    return tmp_path


def test_audit_passes_the_exact_closure_to_pip_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _governed_repo(tmp_path, "fastapi==0.141.1\n")
    # This former input deliberately points at a newer version.  The audit
    # must not resolve it and thereby claim a clean result for a different
    # dependency set.
    broad_requirements = repo_root / "requirements-audit.txt"
    broad_requirements.write_text("fastapi>=0.142.0\n", encoding="utf-8")
    command: list[str] = []

    def record_audit(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        command.extend(arguments)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(audit.subprocess, "run", record_audit)
    monkeypatch.setattr(sys, "argv", ["run_security_audit.py", "--repo-root", str(repo_root)])

    assert audit.main() == 0
    assert command[-1] == str(repo_root / "constraints.txt")
    assert command[-1] != str(broad_requirements)


def test_vulnerable_pinned_version_remains_audited_when_a_newer_floor_is_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scanner sees the vulnerable committed pin, not a newer resolver result."""

    fixture = ROOT / "tests/fixtures/security-audit/vulnerable-pinned-closure.txt"
    vulnerable_pin = fixture.read_text(encoding="utf-8")
    repo_root = _governed_repo(tmp_path, vulnerable_pin)
    newer_floor = repo_root / "requirements-audit.txt"
    newer_floor.write_text("urllib3>=2.7.0\n", encoding="utf-8")

    def scanner(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        audited = Path(arguments[-1]).read_text(encoding="utf-8")
        # Represents pip-audit flagging the selected legacy pin.  If the
        # broad floor were passed instead, this controlled scanner would be
        # green and the regression would be visible.
        return SimpleNamespace(returncode=1 if vulnerable_pin in audited else 0)

    monkeypatch.setattr(audit.subprocess, "run", scanner)
    monkeypatch.setattr(sys, "argv", ["run_security_audit.py", "--repo-root", str(repo_root)])

    assert audit.main() == 1
    assert newer_floor.read_text(encoding="utf-8") == "urllib3>=2.7.0\n"


def test_audit_refuses_a_missing_or_nonexact_closure(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dependency_audit_constraints_missing"):
        audit._audit_closure_file(tmp_path)

    repo_root = _governed_repo(tmp_path, "fastapi>=0.116.1\n")
    with pytest.raises(ValueError, match="dependency_constraints_not_exact"):
        audit._audit_closure_file(repo_root)
