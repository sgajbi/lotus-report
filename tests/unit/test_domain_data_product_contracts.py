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
TRUST_TELEMETRY_PATH = (
    ROOT / "contracts" / "trust-telemetry" / "client-report-evidence-pack.telemetry.v1.json"
)
REPORTING_READ_SERVICE_PATH = ROOT / "src" / "app" / "services" / "reporting_read_service.py"
DECLARATION_README_PATH = ROOT / "contracts" / "domain-data-products" / "README.md"


def _load_consumer_declaration() -> dict:
    return json.loads(CONSUMER_DECLARATION_PATH.read_text(encoding="utf-8"))


def _load_product_declaration() -> dict:
    return json.loads(PRODUCT_DECLARATION_PATH.read_text(encoding="utf-8"))


def _load_trust_telemetry() -> dict:
    return json.loads(TRUST_TELEMETRY_PATH.read_text(encoding="utf-8"))


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
    assert "partially certified" in readme


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
    assert product["approved_consumers"] == ["lotus-gateway", "lotus-idea"]
    assert "/reports/portfolios/{portfolio_id}/review" in product["current_routes"]
    assert product["completeness_policy"] == {
        "default_status": "partial",
        "partial_allowed": True,
        "completion_boundary": (
            "Core portfolio and transaction evidence can be complete under current governed "
            "consumer declarations. Analytics-enriched performance and risk evidence remains "
            "partially certified while lotus-performance and lotus-risk dependencies are "
            "watchlisted."
        ),
    }
    assert (
        "latest certified upstream portfolio, performance, and risk"
        not in product["freshness_policy"]["max_allowed_age_description"]
    )
    assert product["dependency_certification_boundary"] == {
        "governed_consumer_dependencies": [
            "lotus-core:HoldingsAsOf:v1",
            "lotus-core:TransactionLedgerWindow:v1",
        ],
        "watchlisted_analytics_dependencies": [
            "lotus-performance",
            "lotus-risk",
        ],
        "analytics_enriched_evidence_certification": ("partial_until_upstream_consumer_approval"),
        "consumer_safe_summary": (
            "Do not treat performance/risk-enriched ClientReportEvidencePack evidence as "
            "mesh-certified until lotus-performance and lotus-risk producer declarations approve "
            "lotus-report and the consumer declaration is updated."
        ),
    }
    assert product["lineage_policy"]["lineage_required"] is True
    assert product["lineage_policy"]["lineage_bundle_class_ref"] == "customer_lineage_summary"


def test_client_report_evidence_pack_telemetry_surfaces_watchlisted_analytics_boundary() -> None:
    telemetry = _load_trust_telemetry()

    assert telemetry["product_id"] == "lotus-report:ClientReportEvidencePack:v1"
    assert telemetry["completeness_status"] == "partial"
    assert telemetry["data_quality_status"] == "quality_warning"
    assert telemetry["observed_trust_metadata"]["data_quality_status"] == "quality_warning"
    assert telemetry["blocking"] == {
        "blocked": True,
        "blocking_scope": "analytics_enriched_evidence_certification",
        "reason_codes": ["PERFORMANCE_RISK_CONSUMER_APPROVAL_PENDING"],
        "blocked_dependencies": ["lotus-performance", "lotus-risk"],
        "consumer_safe_summary": (
            "Core lotus-core-backed evidence remains usable under current declarations; "
            "analytics-enriched performance/risk evidence is not mesh-certified until upstream "
            "producer declarations approve lotus-report as a governed consumer."
        ),
    }
    assert telemetry["dependency_certification_boundary"] == {
        "governed_consumer_dependencies": [
            "lotus-core:HoldingsAsOf:v1",
            "lotus-core:TransactionLedgerWindow:v1",
        ],
        "watchlisted_analytics_dependencies": ["lotus-performance", "lotus-risk"],
        "analytics_enriched_evidence_certification": ("partial_until_upstream_consumer_approval"),
    }


def test_watchlisted_analytics_dependencies_cannot_publish_complete_unblocked_telemetry() -> None:
    consumer_producers = {
        dependency["producer_repository"]
        for dependency in _load_consumer_declaration()["dependencies"]
    }
    watchlisted = {"lotus-performance", "lotus-risk"} - consumer_producers
    telemetry = _load_trust_telemetry()
    product = _load_product_declaration()["products"][0]

    assert watchlisted == {"lotus-performance", "lotus-risk"}
    assert product["dependency_certification_boundary"][
        "watchlisted_analytics_dependencies"
    ] == sorted(watchlisted)
    assert telemetry["dependency_certification_boundary"][
        "watchlisted_analytics_dependencies"
    ] == sorted(watchlisted)
    assert telemetry["completeness_status"] != "complete"
    assert telemetry["data_quality_status"] != "quality_passed"
    assert telemetry["blocking"]["blocked"] is True
