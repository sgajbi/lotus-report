"""V9 additions to the existing literal workbook and capacity planner."""

from typing import Any


def source_evidence_fields(table: str, row: str, column: str, cell: dict[str, Any]) -> list[str]:
    for side in ("before", "after"):
        prefix = side.capitalize() + "_"
        if table.startswith(prefix):
            return [
                side,
                "composite_review.v8",
                table.removeprefix(prefix),
                row,
                column,
                cell["source_pointer"].removeprefix(f"/generations/{side}/payload"),
            ]
    return ["NOTICE", "", "", "", "", ""]


def notice_artifact_identity(data: dict[str, Any]) -> dict[str, Any]:
    def generation(side: str) -> dict[str, Any]:
        return {
            key: data["generations"][side][key]
            for key in (
                "selection",
                "contract_version",
                "capture_identity",
                "snapshot_response_bytes_sha256",
            )
        }

    return {
        "comparison_profile": data["comparison_profile"],
        "pair_fingerprint": data["pair_fingerprint"],
        "manifest_digest": data["manifest_digest"],
        "before_generation": generation("before"),
        "after_generation": generation("after"),
        "notice_completeness": data["notice_completeness"],
    }
