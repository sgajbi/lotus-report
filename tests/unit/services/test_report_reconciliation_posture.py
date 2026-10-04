"""Report-wide unknown is independent of ready, source-reconciled sections."""

import json
from pathlib import Path

import pytest

from app.main import app
from app.services.reporting_read_service import ReportingReadService
from tests.unit.test_reporting_read_service import (
    _CoreQueryClientSuccess,
    _PerformanceClientSuccess,
    _RiskClientSuccess,
)

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "contracts/domain-data-products/lotus-report-reconciliation-posture.v1.json"


def test_versioned_contract_states_designed_unknown_and_separate_certification():
    contract = json.loads(CONTRACT_PATH.read_text())
    assert contract["contract_version"] == "1.0.0"
    assert contract["product_id"] == "lotus-report:ClientReportEvidencePack:v1"
    assert contract["posture"] == {
        "outcome": "B",
        "reconciliation_status": "unknown",
        "reconciliation_reason_code": "no_reconciliation_policy_established",
        "report_level_verdict_formed": False,
        "designed_steady_state": True,
    }
    assert set(contract["absent_report_level_authorities"]) == {
        "book_of_record",
        "comparand_owner",
        "common_comparison_cut",
        "tolerance",
        "retained_assembled_report_check",
    }
    assert set(contract["prohibited_inferences"]) == {
        "section_readiness",
        "independently_reconciled_sources",
        "canonical_revision_identity",
        "faithful_replay_or_rerender",
    }
    assert contract["certification"]["posture"] == "certification_candidate"
    assert contract["certification"]["unknown_is_certification_approval"] is False
    assert contract["certification"]["promotion_requires"] == [
        "defined_and_proven_authoritative_reconciliation_policy",
        "blocking_platform_gate_from_exact_producer_main",
    ]


def test_actual_review_api_contract_explains_the_reason_for_unknown():
    schema = app.openapi()
    operation = schema["paths"]["/reports/portfolios/{portfolio_id}/review"]["post"]
    response_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_ref["$ref"].endswith("/PortfolioReviewReportResponse")
    description = schema["components"]["schemas"]["PortfolioReviewReportResponse"]["properties"][
        "evidence"
    ]["description"]
    for meaning in (
        "designed steady state",
        "unknown",
        "no_reconciliation_policy_established",
        "book of record",
        "comparand",
        "common cut",
        "tolerance",
        "Ready sections",
        "independently reconciled sources",
        "Certification remains separate",
        "lotus-report-reconciliation-posture.v1.json",
    ):
        assert meaning in description


@pytest.mark.asyncio
@pytest.mark.parametrize("posture", ["ephemeral_composition", "durable_snapshot"])
async def test_actual_ready_report_with_reconciled_sources_keeps_unknown(posture):
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=_PerformanceClientSuccess(),
        risk_client=_RiskClientSuccess(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["HOLDINGS", "TRANSACTIONS"]},
        "posture-correlation",
        admitted_tenant_id="default",
        evidence_posture=posture,
    )
    assert response["readiness"]["status"] == "ready"
    refs = response["evidence"]["source_refs"]
    source_products = [r["source_product"] for r in refs if "source_product" in r]
    assert {p["product_name"] for p in source_products} == {
        "HoldingsAsOf",
        "TransactionLedgerWindow",
    }
    assert all(p["reconciliation_status"] == "RECONCILED" for p in source_products)
    trust = response["evidence"]["trust_metadata"]
    assert trust["completeness_status"] == "complete"
    assert trust["data_quality_status"] == "quality_passed"
    assert trust["reconciliation_status"] == "unknown"
    assert trust["reconciliation_reason_code"] == "no_reconciliation_policy_established"
    assert response["evidence"]["evidence_posture"] == posture
