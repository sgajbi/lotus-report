from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_review_playbook_requires_github_issue_discovery_fields() -> None:
    playbook = _read("docs/architecture/CODEBASE-REVIEW-PLAYBOOK.md")

    assert "GitHub Issue-Discovery Workflow" in playbook
    assert "gh issue list --repo sgajbi/lotus-report --state all --search" in playbook
    assert "duplicate-search proof" in playbook
    assert "acceptance criteria" in playbook
    assert "validation proof" in playbook
    assert "same-pattern scan notes" in playbook
    assert "Finding: #<issue> - <title>" in playbook


def test_review_ledger_cannot_be_active_local_only_backlog() -> None:
    ledger = _read("docs/architecture/CODEBASE-REVIEW-LEDGER.md")

    assert "active validated backlog findings live in GitHub issues" in ledger
    assert "https://github.com/sgajbi/lotus-report/issues/109" in ledger
    assert "local review notes without a linked issue are historical evidence only" in ledger


def test_repo_context_and_wiki_link_review_issue_discovery_workflow() -> None:
    context = _read("REPOSITORY-ENGINEERING-CONTEXT.md")
    wiki = _read("wiki/Development-Workflow.md")

    for text in (context, wiki):
        assert "CODEBASE-REVIEW-PLAYBOOK.md" in text
        assert "CODEBASE-REVIEW-LEDGER.md" in text
        assert "#109" in text
        assert "duplicate" in text.lower()
