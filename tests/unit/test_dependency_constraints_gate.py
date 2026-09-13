"""The constraints gate must be able to FAIL — each refusal direction proven."""

from __future__ import annotations

import pytest

from scripts.check_dependency_constraints import (
    compare_constraints,
    main,
    parse_constraints,
)


def test_matching_closures_are_clean() -> None:
    constrained = parse_constraints("fastapi==0.116.1\npsycopg==3.2.3\n")
    installed = {"fastapi": "0.116.1", "psycopg": "3.2.3", "lotus-report": "0.1.0"}
    assert compare_constraints(constrained, installed, project_name="lotus-report") == []


def test_version_drift_names_the_package_and_both_versions() -> None:
    constrained = parse_constraints("fastapi==0.116.1\n")
    installed = {"fastapi": "0.117.0"}
    assert compare_constraints(constrained, installed, project_name="lotus-report") == [
        "version_drift:fastapi:constrained==0.116.1:installed==0.117.0"
    ]


def test_installed_but_unconstrained_fails() -> None:
    """A package that arrived outside the recorded closure is drift, not bonus."""
    constrained = parse_constraints("fastapi==0.116.1\n")
    installed = {"fastapi": "0.116.1", "left-pad": "1.0.0"}
    assert compare_constraints(constrained, installed, project_name="lotus-report") == [
        "unconstrained_installed:left-pad==1.0.0"
    ]


def test_constrained_but_missing_fails() -> None:
    constrained = parse_constraints("fastapi==0.116.1\nhttpx==0.28.1\n")
    installed = {"fastapi": "0.116.1"}
    assert compare_constraints(constrained, installed, project_name="lotus-report") == [
        "constrained_not_installed:httpx==0.28.1"
    ]


def test_project_itself_and_packaging_tooling_are_excluded() -> None:
    constrained = parse_constraints("fastapi==0.116.1\n")
    installed = {
        "fastapi": "0.116.1",
        "lotus-report": "0.1.0",
        "pip": "25.0",
        "setuptools": "80.0.0",
        "wheel": "0.45.0",
    }
    assert compare_constraints(constrained, installed, project_name="lotus-report") == []


def test_name_normalization_matches_pep503() -> None:
    constrained = parse_constraints("Prometheus_FastAPI-Instrumentator==8.1.0\n")
    installed = {"prometheus-fastapi-instrumentator": "8.1.0"}
    assert compare_constraints(constrained, installed, project_name="lotus-report") == []


def test_a_range_in_the_constraints_file_is_itself_refused() -> None:
    """A reproducibility statement that permits a range reproduces nothing."""
    with pytest.raises(ValueError, match="dependency_constraints_not_exact:fastapi>=0.116"):
        parse_constraints("fastapi>=0.116\n")


def test_comments_and_blanks_are_ignored() -> None:
    constrained = parse_constraints("# closure\n\nfastapi==0.116.1  # pinned\n")
    assert constrained == {"fastapi": "0.116.1"}


def test_non_linux_platform_reports_not_evaluable_without_failing(monkeypatch, capsys) -> None:
    """The platform boundary is explicit output, never a silent pass.

    The recorded closure is resolved for the Linux lanes (environment markers
    make one exact closure platform-specific), so elsewhere the gate states
    not-evaluable — the lotus-gateway Ubuntu-only-gate posture — rather than
    comparing against a closure that is wrong for the host by construction.
    """
    monkeypatch.setattr("scripts.check_dependency_constraints.sys.platform", "win32")

    assert main() == 0
    printed = capsys.readouterr().out
    assert "not evaluable on this platform" in printed
    assert "Linux CI lanes" in printed


def test_missing_constraints_file_fails_closed_on_linux(monkeypatch, tmp_path, capsys) -> None:
    """An unrecorded closure is the defect itself, not a skippable state."""
    monkeypatch.setattr("scripts.check_dependency_constraints.sys.platform", "linux")
    monkeypatch.chdir(tmp_path)

    assert main() == 1
    assert "constraints.txt is missing" in capsys.readouterr().err
