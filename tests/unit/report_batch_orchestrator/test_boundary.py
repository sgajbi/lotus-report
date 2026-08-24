from pathlib import Path

from app.report_batch_orchestrator import (
    BATCH_CAPABILITY_KEY,
    BATCH_CONTROL_API_CAPABILITY_KEY,
    BATCH_FREQUENCIES,
    BATCH_MATERIALIZATION_API_CAPABILITY_KEY,
    BATCH_RUNTIME_SUPPORTED,
    BATCH_SCHEDULER_ADMIN_API_CAPABILITY_KEY,
    BATCH_SELECTOR_MODES,
)

ROOT = Path(__file__).resolve().parents[3]


def test_batch_orchestrator_boundary_matches_rfc_0104_first_wave_vocabulary() -> None:
    assert BATCH_CAPABILITY_KEY == "lotus-report.reporting.batch_orchestration.v1"
    assert (
        BATCH_MATERIALIZATION_API_CAPABILITY_KEY
        == "lotus-report.reporting.batch_materialization_api.v1"
    )
    assert BATCH_CONTROL_API_CAPABILITY_KEY == "lotus-report.reporting.batch_control_api.v1"
    assert (
        BATCH_SCHEDULER_ADMIN_API_CAPABILITY_KEY
        == "lotus-report.reporting.batch_scheduler_admin_api.v1"
    )
    assert BATCH_RUNTIME_SUPPORTED is False
    assert BATCH_SELECTOR_MODES == (
        "explicit_portfolio_list",
        "selected_subset",
        "all_active_portfolios",
        "batch_manifest",
    )
    assert BATCH_FREQUENCIES == (
        "monthly",
        "quarterly",
        "semi_annual",
        "yearly",
        "explicit",
    )


def test_supported_features_do_not_claim_batch_runtime_support() -> None:
    supported_features = (ROOT / "docs/supported-features.md").read_text(encoding="utf-8")
    implementation_backed = supported_features.split(
        "## Planned RFC-0104 Feature Candidates", maxsplit=1
    )[0]

    assert BATCH_CAPABILITY_KEY not in implementation_backed
    assert "`lotus-report.reporting.batch_scheduler.v1`" not in implementation_backed
    assert "`lotus-report.reporting.batch_scheduler_admin_api.v1`" in implementation_backed
    assert "`lotus-report.reporting.batch_scheduler_process.v1`" in implementation_backed
    assert "worker runtime" not in implementation_backed.lower()


def test_batch_archive_document_handoff_is_documented_as_source_owned_and_fail_closed() -> None:
    supported_features = (ROOT / "docs/supported-features.md").read_text(encoding="utf-8")
    source_map = (ROOT / "docs/standards/batch-orchestration-source-map.md").read_text(
        encoding="utf-8"
    )
    api_surface = (ROOT / "wiki/API-Surface.md").read_text(encoding="utf-8")
    operations = (ROOT / "wiki/Operations-Runbook.md").read_text(encoding="utf-8")
    context = (ROOT / "REPOSITORY-ENGINEERING-CONTEXT.md").read_text(encoding="utf-8")

    for text in (supported_features, source_map, api_surface, operations, context):
        assert "archive_document_id" in text or "archive document id" in text
        assert "archived" in text

    assert "never inferred by batch status" in source_map
    assert "never derive a document id" in operations
    assert "corrections and replacements" in operations
    assert "source-owned archive document id only after" in context
