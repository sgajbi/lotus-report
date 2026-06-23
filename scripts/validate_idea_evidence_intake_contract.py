from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "contracts" / "idea-evidence-intake" / "lotus-report-idea-evidence-pack-intake.v1.json"
)
PRODUCTS_PATH = ROOT / "contracts" / "domain-data-products" / "lotus-report-products.v1.json"

REQUIRED_FIELDS = {
    "report_evidence_pack_id",
    "conversion_intent_id",
    "candidate_id",
    "purpose",
    "evidence_packet_id",
    "evidence_content_fingerprint",
    "source_signal_ids",
    "source_summaries",
    "reason_codes",
    "report_source_authority",
    "render_source_authority",
    "archive_source_authority",
    "boundary",
    "retention_policy_ref",
    "requested_at_utc",
    "grants_client_publication_authority",
    "creates_rendered_output",
    "creates_archive_record",
    "producer",
    "supportability_status",
}
FORBIDDEN_FIELD_FRAGMENTS = {
    "client_name",
    "clientname",
    "account_number",
    "accountnumber",
    "holding_id",
    "holdingid",
    "transaction_id",
    "transactionid",
    "request_body",
    "requestbody",
    "response_body",
    "responsebody",
    "raw_prompt",
    "rawprompt",
    "raw_provider_response",
    "rawproviderresponse",
}
REQUIRED_BLOCKERS = {
    "lotus_report_live_intake_route_proof_missing",
    "report_evidence_pack_live_materialization_proof_missing",
    "rendered_output_creation_missing",
    "archive_record_creation_missing",
    "client_publication_authority_blocked",
}


def validate_idea_evidence_intake_contract(
    *,
    contract_path: Path = CONTRACT_PATH,
    products_path: Path = PRODUCTS_PATH,
) -> list[str]:
    contract = _load_object(contract_path, "idea evidence intake contract")
    products = _load_object(products_path, "lotus-report product declaration")
    errors: list[str] = []

    errors.extend(_validate_contract_identity(contract))
    errors.extend(_validate_source_authority(contract, products))
    errors.extend(_validate_payload_fields(contract))
    errors.extend(_validate_boundaries(contract))
    errors.extend(_validate_paths(contract))
    return errors


def _validate_contract_identity(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_values = {
        "contract_id": "lotus-report-idea-evidence-pack-intake",
        "contract_version": "1.0.0",
        "repository": "lotus-report",
        "owner_repository": "lotus-report",
        "approved_producer_repository": "lotus-idea",
        "approved_producer_product": "lotus-idea:IdeaEvidencePacket:v1",
        "owned_product": "lotus-report:ClientReportEvidencePack:v1",
        "lifecycle_status": "planned",
        "supportability_status": "not_certified",
        "target_route": "planned:lotus-report-idea-evidence-pack-intake",
    }
    for key, expected in expected_values.items():
        if contract.get(key) != expected:
            errors.append(f"{key} must be {expected}")

    for key in (
        "route_existence_proven",
        "materialization_proven",
        "supported_feature_promoted",
    ):
        if contract.get(key) is not False:
            errors.append(f"{key} must remain false until live proof exists")
    return errors


def _validate_source_authority(
    contract: dict[str, Any],
    products: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    authority = contract.get("source_authority")
    if not isinstance(authority, dict):
        return ["source_authority must be an object"]
    expected_authority = {
        "idea_evidence": "lotus-idea",
        "report_materialization": "lotus-report",
        "rendering": "lotus-render",
        "archive_record": "lotus-archive",
    }
    for key, expected in expected_authority.items():
        if authority.get(key) != expected:
            errors.append(f"source_authority.{key} must be {expected}")

    if contract.get("approved_producer_repository") != authority.get("idea_evidence"):
        errors.append("approved_producer_repository must match source_authority.idea_evidence")
    approved_product = contract.get("approved_producer_product")
    if not isinstance(approved_product, str) or not approved_product.startswith("lotus-idea:"):
        errors.append("approved_producer_product must identify a lotus-idea evidence product")

    report_product = _client_report_evidence_pack(products)
    if report_product.get("lifecycle_status") != "active":
        errors.append("ClientReportEvidencePack producer declaration must remain active")
    return errors


def _validate_payload_fields(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_fields = _string_set(contract.get("required_payload_fields"))
    missing_fields = sorted(REQUIRED_FIELDS - required_fields)
    if missing_fields:
        errors.append("required_payload_fields missing: " + ", ".join(missing_fields))

    forbidden_fields = _string_set(contract.get("forbidden_payload_fields"))
    normalized_forbidden = _normalized_fields(forbidden_fields)
    missing_forbidden = sorted(
        fragment for fragment in FORBIDDEN_FIELD_FRAGMENTS if fragment not in normalized_forbidden
    )
    if missing_forbidden:
        errors.append(
            "forbidden_payload_fields missing sensitive fragments: " + ", ".join(missing_forbidden)
        )

    normalized_required = _normalized_fields(required_fields)
    camel_case_required = sorted(
        field for field in required_fields if any(character.isupper() for character in field)
    )
    if camel_case_required:
        errors.append(
            "required_payload_fields must use canonical snake_case names: "
            + ", ".join(camel_case_required)
        )
    sensitive_required_fields = _required_fields_with_forbidden_fragments(
        normalized_required,
        normalized_forbidden,
    )
    if sensitive_required_fields:
        errors.append(
            "required_payload_fields must not include forbidden sensitive fragments: "
            + ", ".join(sensitive_required_fields)
        )
    return errors


def _validate_boundaries(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    blockers = _string_set(contract.get("certification_blockers"))
    missing_blockers = sorted(REQUIRED_BLOCKERS - blockers)
    if missing_blockers:
        errors.append("certification_blockers missing: " + ", ".join(missing_blockers))

    boundaries = _string_set(contract.get("non_proof_boundaries"))
    required_terms = (
        "live lotus-report intake route",
        "rendered document",
        "archive record",
        "supported feature",
    )
    for term in required_terms:
        if not any(term in boundary for boundary in boundaries):
            errors.append(f"non_proof_boundaries must mention {term}")
    return errors


def _validate_paths(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    evidence_refs = _string_set(contract.get("evidence_refs"))
    for relative_path in evidence_refs:
        if not relative_path.startswith(("contracts/", "scripts/", "tests/")):
            continue
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            errors.append(f"evidence ref must stay repository relative: {relative_path}")
        elif not (ROOT / path).exists():
            errors.append(f"evidence ref path missing: {relative_path}")
    return errors


def _client_report_evidence_pack(products: dict[str, Any]) -> dict[str, Any]:
    declarations = products.get("products")
    if not isinstance(declarations, list):
        return {}
    for product in declarations:
        if isinstance(product, dict) and product.get("product_name") == "ClientReportEvidencePack":
            return product
    return {}


def _load_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _normalized_fields(fields: set[str]) -> set[str]:
    return {field.replace("-", "_").lower() for field in fields}


def _required_fields_with_forbidden_fragments(
    required_fields: set[str],
    forbidden_fragments: set[str],
) -> list[str]:
    return sorted(
        field
        for field in required_fields
        if any(
            fragment in field or fragment.replace("_", "") in field.replace("_", "")
            for fragment in forbidden_fragments
        )
    )


def main() -> int:
    errors = validate_idea_evidence_intake_contract()
    if errors:
        print("\n".join(errors))
        return 1
    print("Idea evidence intake contract gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
