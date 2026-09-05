"""ClientReportEvidencePack construction - the evidence/trust seam.

Extracted from the reporting read service as one coherent, framework-free
responsibility (report#283 review instruction): the pack's identity labels,
source refs, and trust metadata are built here and nowhere else.

Trust claims state only what is PROVEN:
- the tenant is the ADMITTED caller tenant, never a hardcoded "default";
  an unattributed caller yields no tenant claim at all, with the admission
  posture saying why (source-verified tenancy arrives with Core #177);
- no reconciliation policy exists yet, so reconciliation_status is
  "unknown" with a bounded reason - "reconciled" was never proven;
- evidence_posture distinguishes the synchronous ephemeral composition
  from a durably captured snapshot: the two flows must not publish
  indistinguishable evidence claims.

The portfolio/date-derived bundle labels below are SERIES-grade
correlation handles, not revision or evidence identifiers: every capture
of the same logical request shares them. The canonical report-revision
identity is minted at capture and persisted BESIDE the durable snapshot
(report#283) - it can never live inside this pack, because the pack is
part of the hashed payload the identity derives from. The synthetic
source_batch_fingerprint the pack once fabricated is retired; a source
fingerprint appears only where a source actually states one.
"""

from __future__ import annotations


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _safe_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def build_review_evidence(
    *,
    portfolio_id: str,
    as_of_date: str,
    correlation_id: str | None,
    response: dict[str, object],
    admitted_tenant_id: str | None,
    evidence_posture: str,
) -> dict[str, object]:
    source_refs = _review_source_refs(portfolio_id=portfolio_id, response=response)
    source_services = sorted(
        {
            _safe_str(source_ref.get("source_service"))
            for source_ref in source_refs
            if _safe_str(source_ref.get("source_service"))
        }
    )
    lineage_bundle_id = f"lineage:lotus-report:portfolio-review:{portfolio_id}:{as_of_date}"
    evidence_bundle_id = f"evidence:lotus-report:portfolio-review:{portfolio_id}:{as_of_date}"
    readiness_status = _as_dict(response.get("readiness")).get("status", "ready")
    completeness_status = "partial" if readiness_status == "partial" else "complete"
    data_quality_status = "quality_warning" if readiness_status == "partial" else "quality_passed"
    trust_metadata: dict[str, object] = {
        "product_name": "ClientReportEvidencePack",
        "product_version": "v1",
        "generated_at": response.get("generated_at"),
        "as_of_date": as_of_date,
        "completeness_status": completeness_status,
        "reconciliation_status": "unknown",
        "reconciliation_reason_code": "no_reconciliation_policy_established",
        "data_quality_status": data_quality_status,
        # source_batch_fingerprint is RETIRED here: the pack used to carry a
        # portfolio/date label under that name, which fabricated a source
        # claim no source ever stated. Genuine source-stated fingerprints
        # remain wherever a source states them (sourceProduct blocks); the
        # canonical revision identity lives BESIDE the durable snapshot,
        # never inside the pack (no circular identity).
        "lineage_bundle_id": lineage_bundle_id,
        "correlation_id": correlation_id,
    }
    if admitted_tenant_id:
        trust_metadata["tenant_id"] = admitted_tenant_id
        trust_metadata["tenant_admission"] = "caller_admitted"
    else:
        trust_metadata["tenant_admission"] = "unattributed_caller"
    return {
        "product_id": "lotus-report:ClientReportEvidencePack:v1",
        "product_name": "ClientReportEvidencePack",
        "product_version": "v1",
        "lineage_bundle_id": lineage_bundle_id,
        "evidence_bundle_id": evidence_bundle_id,
        "evidence_access_class": "customer_consumable",
        "evidence_posture": evidence_posture,
        "portfolio_id": portfolio_id,
        "as_of_date": as_of_date,
        "correlation_id": correlation_id,
        "source_services": source_services,
        "source_refs": source_refs,
        "trust_metadata": trust_metadata,
    }


def _review_source_refs(
    *, portfolio_id: str, response: dict[str, object]
) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    _append_source_ref(
        refs,
        response=response,
        response_key="clientProfile",
        section_id="client_profile",
        source_service="lotus-core",
        source_endpoint=f"/portfolios/{portfolio_id}",
        source_entity_id=portfolio_id,
    )
    _append_source_ref(
        refs,
        response=response,
        response_key="overview",
        section_id="executive_summary",
        source_service="lotus-core",
        source_endpoint="/reporting/portfolio-summary/query",
        source_entity_id=portfolio_id,
    )
    _append_source_ref(
        refs,
        response=response,
        response_key="allocation",
        section_id="asset_allocation",
        source_service="lotus-core",
        source_endpoint="/reporting/asset-allocation/query",
        source_entity_id=portfolio_id,
    )
    _append_source_ref(
        refs,
        response=response,
        response_key="performance",
        section_id="performance_review",
        source_service="lotus-performance",
        source_endpoint="/performance/workspace-summary",
        source_entity_id=portfolio_id,
    )
    _append_source_ref(
        refs,
        response=response,
        response_key="riskAnalytics",
        section_id="risk_review",
        source_service="lotus-risk",
        source_endpoint="/analytics/risk/calculate",
        source_entity_id=portfolio_id,
        input_services=["lotus-performance"],
    )
    _append_source_ref(
        refs,
        response=response,
        response_key="incomeAndActivity",
        section_id="income_cash_activity",
        source_service="lotus-core",
        source_endpoint=f"/portfolios/{portfolio_id}/transactions",
        source_entity_id=portfolio_id,
        source_product=_as_dict(_as_dict(response.get("incomeAndActivity")).get("sourceProduct")),
    )
    _append_source_ref(
        refs,
        response=response,
        response_key="holdings",
        section_id="holdings_appendix",
        source_service="lotus-core",
        source_endpoint=f"/portfolios/{portfolio_id}/positions",
        source_entity_id=portfolio_id,
        source_product=_as_dict(_as_dict(response.get("holdings")).get("sourceProduct")),
    )
    _append_source_ref(
        refs,
        response=response,
        response_key="transactions",
        section_id="transactions_appendix",
        source_service="lotus-core",
        source_endpoint=f"/portfolios/{portfolio_id}/transactions",
        source_entity_id=portfolio_id,
        source_product=_as_dict(_as_dict(response.get("transactions")).get("sourceProduct")),
    )
    return refs


def _append_source_ref(
    refs: list[dict[str, object]],
    *,
    response: dict[str, object],
    response_key: str,
    section_id: str,
    source_service: str,
    source_endpoint: str,
    source_entity_id: str,
    input_services: list[str] | None = None,
    source_product: dict[str, object] | None = None,
) -> None:
    if response_key not in response:
        return
    source_ref: dict[str, object] = {
        "section_id": section_id,
        "response_key": response_key,
        "source_service": source_service,
        "source_endpoint": source_endpoint,
        "source_entity_id": source_entity_id,
    }
    if input_services:
        source_ref["input_services"] = input_services
    if source_product:
        source_ref["source_product"] = source_product
    refs.append(source_ref)
