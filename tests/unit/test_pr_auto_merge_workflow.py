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


GATEWAY_REFERENCE_NOTE = (
    "lotus-gateway is the reference implementation; divergence here reintroduces the ungated-main "
    "defect recorded in issue #180."
)


def test_auto_merge_uses_a_token_that_can_trigger_downstream_workflows() -> None:
    """GITHUB_TOKEN pushes do not trigger workflow runs.

    With `github.token`, the merge push to `main` is not an eligible trigger, so
    `main-releasability.yml` never runs for an automated merge. That produced a `main` tip with
    zero releasability evidence (#180).
    """

    workflow = (WORKFLOW_ROOT / "pr-auto-merge.yml").read_text(encoding="utf-8")

    assert "secrets.LOTUS_AUTOMERGE_TOKEN" in workflow, GATEWAY_REFERENCE_NOTE
    assert "GH_TOKEN: ${{ github.token }}" not in workflow, GATEWAY_REFERENCE_NOTE


def test_auto_merge_fails_visibly_when_the_token_is_absent() -> None:
    """A missing secret must not silently fall back to an ineligible token."""

    workflow = (WORKFLOW_ROOT / "pr-auto-merge.yml").read_text(encoding="utf-8")

    assert 'if [ -z "$GH_TOKEN" ]; then' in workflow
    assert "::warning::LOTUS_AUTOMERGE_TOKEN is required" in workflow


def test_auto_merge_requests_no_more_permission_than_it_needs() -> None:
    workflow = (WORKFLOW_ROOT / "pr-auto-merge.yml").read_text(encoding="utf-8")

    assert "permissions:\n  contents: read\n" in workflow
    assert "contents: write" not in workflow
    assert "timeout-minutes:" in workflow


def test_a_dispatcher_exists_so_the_gate_does_not_depend_on_the_push_trigger() -> None:
    """The token fix and the dispatcher are both required; either alone is one point of failure."""

    dispatcher_path = WORKFLOW_ROOT / "merged-pr-main-releasability.yml"

    assert dispatcher_path.is_file(), (
        "merged-pr-main-releasability.yml is the fallback that runs the gate when the merge push "
        "does not trigger it. " + GATEWAY_REFERENCE_NOTE
    )
    dispatcher = dispatcher_path.read_text(encoding="utf-8")
    assert "types: [closed]" in dispatcher
    assert "pull_request.merged == true" in dispatcher
    assert "gh workflow run main-releasability.yml" in dispatcher
    assert "expected_sha" in dispatcher


def test_main_releasability_validates_the_exact_dispatched_revision() -> None:
    """A dispatched run must prove it validated the merge commit, not whatever main became."""

    workflow = (WORKFLOW_ROOT / "main-releasability.yml").read_text(encoding="utf-8")

    assert "expected_sha:" in workflow
    assert "exact-revision-assertion:" in workflow
    assert "does not match expected merged PR SHA" in workflow
    # Every substantive job gates on the assertion rather than running beside it.
    assert workflow.count("needs: [exact-revision-assertion]") >= 2


def test_main_releasability_is_dispatch_only() -> None:
    """Dispatch-only is deliberate: a suppressed push trigger is silent, a failed dispatch is not.

    Keeping `push` alongside the dispatcher would run the gate twice for every human merge while
    adding no signal that the dispatcher does not already give.
    """

    workflow = (WORKFLOW_ROOT / "main-releasability.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert 'branches: [ "main" ]' not in workflow
