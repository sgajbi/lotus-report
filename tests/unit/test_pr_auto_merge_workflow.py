from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pr_auto_merge_workflow_uses_linear_rebase_merge_strategy() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pr-auto-merge.yml").read_text(encoding="utf-8")

    assert "gh pr merge" in workflow
    assert "--auto --rebase --delete-branch" in workflow
    assert "--auto --merge" not in workflow
