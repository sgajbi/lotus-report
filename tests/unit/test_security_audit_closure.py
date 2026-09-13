"""The security audit must inspect the dependency closure CI actually installs."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_security_audit as audit

ROOT = Path(__file__).resolve().parents[2]
VULNERABLE_PROJECT_FIXTURE = ROOT / "tests/fixtures/security-audit/vulnerable-pinned-project"


def _governed_repo(tmp_path: Path, closure: str, project: str) -> Path:
    exception_file = tmp_path / "docs" / "standards" / "dependency-vulnerability-exceptions.json"
    exception_file.parent.mkdir(parents=True)
    exception_file.write_text(json.dumps({"exceptions": []}), encoding="utf-8")
    (tmp_path / "constraints.txt").write_text(closure, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(project, encoding="utf-8")
    return tmp_path


def _project_metadata(
    *dependencies: str,
    build_dependencies: tuple[str, ...] = (),
    dev_dependencies: tuple[str, ...] = (),
    other_extra_dependencies: tuple[str, ...] = (),
) -> str:
    def toml_list(values: tuple[str, ...]) -> str:
        return ", ".join(json.dumps(value) for value in values)

    return "\n".join(
        [
            "[build-system]",
            f"requires = [{toml_list(build_dependencies)}]",
            'build-backend = "setuptools.build_meta"',
            "",
            "[project]",
            'name = "lotus-report"',
            f"dependencies = [{toml_list(tuple(dependencies))}]",
            "[project.optional-dependencies]",
            f"dev = [{toml_list(dev_dependencies)}]",
            f"other = [{toml_list(other_extra_dependencies)}]",
            "",
        ]
    )


def _matching_environment(project_name: str = "lotus-report", **versions: str) -> dict[str, str]:
    return {project_name: "0.1.0", **versions}


def test_audit_passes_the_exact_closure_to_pip_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _governed_repo(
        tmp_path,
        "fastapi==0.141.1\n",
        _project_metadata("fastapi>=0.116.1"),
    )
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
    monkeypatch.setattr(
        audit,
        "installed_distributions",
        lambda: _matching_environment(fastapi="0.141.1"),
    )
    monkeypatch.setattr(sys, "argv", ["run_security_audit.py", "--repo-root", str(repo_root)])

    assert audit.main() == 0
    assert command[-1] == str(repo_root / "constraints.txt")
    assert command[-1] != str(broad_requirements)


def test_vulnerable_pinned_version_remains_audited_when_a_newer_floor_is_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scanner sees the vulnerable committed pin, not a newer resolver result."""

    repo_root = tmp_path / "vulnerable-pinned-project"
    shutil.copytree(VULNERABLE_PROJECT_FIXTURE, repo_root)
    vulnerable_pin = (repo_root / "constraints.txt").read_text(encoding="utf-8")
    newer_floor = repo_root / "requirements-audit.txt"
    newer_floor.write_text("urllib3>=2.7.0\n", encoding="utf-8")

    def scanner(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        audited = Path(arguments[-1]).read_text(encoding="utf-8")
        # Represents pip-audit flagging the selected legacy pin.  If the
        # broad floor were passed instead, this controlled scanner would be
        # green and the regression would be visible.
        return SimpleNamespace(returncode=1 if vulnerable_pin in audited else 0)

    monkeypatch.setattr(audit.subprocess, "run", scanner)
    monkeypatch.setattr(
        audit,
        "installed_distributions",
        lambda: _matching_environment(
            "lotus-report-vulnerability-fixture",
            urllib3="1.25",
        ),
    )
    monkeypatch.setattr(sys, "argv", ["run_security_audit.py", "--repo-root", str(repo_root)])

    assert audit.main() == 1
    assert newer_floor.read_text(encoding="utf-8") == "urllib3>=2.7.0\n"


def test_audit_refuses_a_missing_or_nonexact_closure(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dependency_audit_constraints_missing"):
        audit._audit_closure_file(tmp_path)

    repo_root = _governed_repo(
        tmp_path,
        "fastapi>=0.116.1\n",
        _project_metadata("fastapi>=0.116.1"),
    )
    with pytest.raises(ValueError, match="dependency_constraints_not_exact"):
        audit._audit_closure_file(repo_root)


def test_audit_refuses_a_missing_declared_direct_dependency(tmp_path: Path) -> None:
    repo_root = _governed_repo(
        tmp_path,
        "uvicorn==0.52.4\n",
        _project_metadata("fastapi>=0.116.1", "uvicorn[standard]>=0.35.0"),
    )

    with pytest.raises(
        ValueError,
        match="dependency_audit_constraints_incomplete:missing_project_pins:fastapi",
    ):
        audit._audit_closure_file(repo_root)


def test_audit_refuses_an_unpinned_declared_build_requirement(tmp_path: Path) -> None:
    repo_root = _governed_repo(
        tmp_path,
        "fastapi==0.141.1\n",
        _project_metadata(
            "fastapi>=0.116.1",
            build_dependencies=("setuptools>=61.0",),
        ),
    )

    with pytest.raises(
        ValueError,
        match="dependency_audit_constraints_incomplete:missing_project_pins:setuptools",
    ):
        audit._audit_closure_file(repo_root)


def test_audit_refuses_an_extra_derived_package_missing_from_the_closure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _governed_repo(
        tmp_path,
        "uvicorn==0.52.4\n",
        _project_metadata("uvicorn[standard]>=0.35.0"),
    )
    monkeypatch.setattr(
        audit,
        "installed_distributions",
        lambda: _matching_environment(uvicorn="0.52.4", httptools="0.8.0"),
    )

    with pytest.raises(
        ValueError,
        match="resolved_environment_drift:unconstrained_installed:httptools==0.8.0",
    ):
        audit._audit_closure_file(repo_root)


def test_audit_accepts_a_complete_project_and_extra_closure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _governed_repo(
        tmp_path,
        "fastapi==0.141.1\nuvicorn==0.52.4\nhttptools==0.8.0\n",
        _project_metadata("fastapi>=0.116.1", "uvicorn[standard]>=0.35.0"),
    )
    monkeypatch.setattr(
        audit,
        "installed_distributions",
        lambda: _matching_environment(fastapi="0.141.1", uvicorn="0.52.4", httptools="0.8.0"),
    )

    assert audit._audit_closure_file(repo_root) == repo_root / "constraints.txt"


def test_audit_only_requires_the_extra_that_the_production_target_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _governed_repo(
        tmp_path,
        "fastapi==0.141.1\n",
        _project_metadata(
            "fastapi>=0.116.1",
            other_extra_dependencies=("uninstalled-extra>=1.0",),
        ),
    )
    monkeypatch.setattr(
        audit,
        "installed_distributions",
        lambda: _matching_environment(fastapi="0.141.1"),
    )

    assert audit._audit_closure_file(repo_root) == repo_root / "constraints.txt"
