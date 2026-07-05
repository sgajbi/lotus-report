from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"


def test_pr_auto_merge_workflow_uses_linear_rebase_merge_strategy() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pr-auto-merge.yml").read_text(encoding="utf-8")

    assert "gh pr merge" in workflow
    assert "--auto --rebase --delete-branch" in workflow
    assert "--auto --merge" not in workflow


def test_ci_workflows_route_tests_and_coverage_through_make_targets() -> None:
    feature_lane = (WORKFLOW_ROOT / "feature-lane.yml").read_text(encoding="utf-8")
    pr_merge_gate = (WORKFLOW_ROOT / "pr-merge-gate.yml").read_text(encoding="utf-8")
    main_releasability = (WORKFLOW_ROOT / "main-releasability.yml").read_text(encoding="utf-8")

    assert "run: make test-unit" in feature_lane
    for workflow in (pr_merge_gate, main_releasability):
        assert "make test-suite-coverage" in workflow
        assert "make coverage-gate" in workflow
        assert "python -m pytest" not in workflow
        assert "python -m coverage" not in workflow


def test_makefile_exposes_repo_native_coverage_targets() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "test-suite-coverage:" in makefile
    assert "coverage-gate:" in makefile
    assert "COVERAGE_INPUTS ?=" in makefile
