from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_domain_data_product_contracts import (
    LOCAL_DECLARATION_DIR,
    platform_validation_dependencies_available,
    validate_repo_native_contracts,
)

ROOT = Path(__file__).resolve().parents[2]
CONSUMER_DECLARATION_PATH = (
    ROOT / "contracts" / "domain-data-products" / "lotus-report-consumers.v1.json"
)
PRODUCT_DECLARATION_PATH = (
    ROOT / "contracts" / "domain-data-products" / "lotus-report-products.v1.json"
)
REPORTING_READ_SERVICE_PATH = ROOT / "src" / "app" / "services" / "reporting_read_service.py"
DECLARATION_README_PATH = ROOT / "contracts" / "domain-data-products" / "README.md"


def _load_consumer_declaration() -> dict:
    return json.loads(CONSUMER_DECLARATION_PATH.read_text(encoding="utf-8"))


def _load_product_declaration() -> dict:
    return json.loads(PRODUCT_DECLARATION_PATH.read_text(encoding="utf-8"))


def test_repo_native_domain_data_product_validation_passes_when_platform_is_available() -> None:
    if not platform_validation_dependencies_available(LOCAL_DECLARATION_DIR):
        pytest.skip("sibling lotus-platform contract validator is not available")

    assert validate_repo_native_contracts() == []


def test_report_consumer_declaration_tracks_current_core_reporting_inputs() -> None:
    payload = _load_consumer_declaration()
    dependencies = payload["dependencies"]
    dependency_names = {dependency["product_name"] for dependency in dependencies}
    producer_repositories = {dependency["producer_repository"] for dependency in dependencies}

    assert payload["consumer_repository"] == "lotus-report"
    assert dependency_names == {"HoldingsAsOf", "TransactionLedgerWindow"}
    assert producer_repositories == {"lotus-core"}

    service_source = REPORTING_READ_SERVICE_PATH.read_text(encoding="utf-8")
    assert "get_portfolio_summary" in service_source
    assert "get_portfolio_positions" in service_source
    assert "get_portfolio_transactions" in service_source


def test_report_declaration_requires_governed_trust_metadata_for_every_dependency() -> None:
    dependencies = _load_consumer_declaration()["dependencies"]
    required_metadata = {
        "product_name",
        "product_version",
        "generated_at",
        "as_of_date",
        "reconciliation_status",
        "data_quality_status",
        "correlation_id",
    }

    for dependency in dependencies:
        assert required_metadata.issubset(set(dependency["required_trust_metadata"]))
        assert dependency["validation_lanes"] == ["feature", "pr-merge"]
        assert dependency["failure_posture"] == "fail_closed"
        assert dependency["migration_posture"] == {"status": "current"}


def test_report_declaration_keeps_unapproved_analytics_dependencies_on_the_watchlist() -> None:
    dependencies = _load_consumer_declaration()["dependencies"]
    producer_repositories = {dependency["producer_repository"] for dependency in dependencies}
    readme = DECLARATION_README_PATH.read_text(encoding="utf-8")

    assert "lotus-performance" not in producer_repositories
    assert "lotus-risk" not in producer_repositories
    assert "approve `lotus-report` as a governed consumer" in readme


def test_report_declaration_directory_contains_consumer_and_owned_product_contracts() -> None:
    declaration_paths = sorted(path.name for path in LOCAL_DECLARATION_DIR.glob("*.json"))

    assert declaration_paths == [
        "lotus-report-consumers.v1.json",
        "lotus-report-products.v1.json",
    ]


def test_report_product_declaration_publishes_client_report_evidence_pack() -> None:
    payload = _load_product_declaration()
    products = payload["products"]

    assert payload["producer_repository"] == "lotus-report"
    assert [product["product_name"] for product in products] == ["ClientReportEvidencePack"]

    product = products[0]
    assert product["product_version"] == "v1"
    assert product["lifecycle_status"] == "active"
    assert product["approved_consumers"] == ["lotus-gateway"]
    assert product["lineage_policy"]["lineage_required"] is True
    assert product["lineage_policy"]["lineage_bundle_class_ref"] == "customer_lineage_summary"
