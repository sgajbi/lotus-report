"""Keep coverage artifact workflows on supported Node 24 action majors."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("workflow_path", "expected_actions"),
    [
        (
            ".github/workflows/pr-merge-gate.yml",
            ["actions/upload-artifact@v7", "actions/download-artifact@v8"],
        ),
        (
            ".github/workflows/main-releasability.yml",
            [
                "actions/upload-artifact@v7",
                "actions/download-artifact@v8",
                "actions/upload-artifact@v7",
            ],
        ),
    ],
)
def test_coverage_artifact_actions_use_node_24_majors(
    workflow_path: str,
    expected_actions: list[str],
) -> None:
    workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
    artifact_actions = re.findall(r"actions/(?:upload|download)-artifact@v\d+", workflow)

    assert artifact_actions == expected_actions
