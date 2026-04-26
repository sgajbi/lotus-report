from pathlib import Path

from app.report_batch_orchestrator import (
    BATCH_CAPABILITY_KEY,
    BATCH_FREQUENCIES,
    BATCH_RUNTIME_SUPPORTED,
    BATCH_SELECTOR_MODES,
)

ROOT = Path(__file__).resolve().parents[3]


def test_batch_orchestrator_boundary_matches_rfc_0104_first_wave_vocabulary() -> None:
    assert BATCH_CAPABILITY_KEY == "lotus-report.reporting.batch_orchestration.v1"
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
    assert "batch scheduler" not in implementation_backed.lower()
    assert "batch orchestration" not in implementation_backed.lower()
