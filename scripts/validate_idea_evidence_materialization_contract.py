from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "contracts"
    / "idea-evidence-materialization"
    / "lotus-report-idea-evidence-pack-materialization.v1.json"
)

REQUIRED_TOP_LEVEL = {
    "contract_id": "lotus-report-idea-evidence-pack-materialization",
    "repository": "lotus-report",
    "approved_producer_repository": "lotus-idea",
    "approved_producer_product": "lotus-idea:IdeaEvidencePacket:v1",
    "owned_product": "lotus-report:ClientReportEvidencePack:v1",
    "lifecycle_status": "implemented",
    "supportability_status": "not_certified",
    "route_existence_proven": True,
    "materialization_proven": True,
    "rendered_output_creation_proven": True,
    "archive_record_creation_proven": True,
    "client_publication_authority_granted": False,
    "supported_feature_promoted": False,
    "target_route": "POST /reports/idea-evidence-packs/materializations",
}

REQUIRED_FIELDS = {
    "idea_evidence_pack",
    "portfolio_id",
    "as_of_date",
    "requested_output_formats",
    "grants_client_publication_authority",
}

REQUIRED_NESTED_IDEA_FIELDS = {
    "report_evidence_pack_id",
    "conversion_intent_id",
    "candidate_id",
    "evidence_packet_id",
    "evidence_content_fingerprint",
    "source_summaries",
    "reason_codes",
}

REQUIRED_RESPONSE_FIELDS = {
    "report_request_id",
    "report_job_id",
    "status",
    "materialization_status",
    "status_url",
    "idempotency_key",
    "report_package_identity",
    "producer",
    "source_authority",
    "materialization_proven",
    "creates_report_job",
    "creates_rendered_output",
    "creates_archive_record",
    "grants_client_publication_authority",
    "supported_feature_promoted",
    "supportability_status",
    "remaining_blockers",
    "evidence_refs",
    "render_job_id",
    "archive_document_id",
}

REQUIRED_REPORT_PACKAGE_IDENTITY_FIELDS = {
    "report_evidence_pack_id",
    "conversion_intent_id",
    "candidate_id",
    "evidence_packet_id",
    "evidence_content_fingerprint",
    "source_contract_version",
    "owned_product",
}

REMAINING_BLOCKERS = {
    "client_publication_authority_blocked",
    "supported_feature_promotion_missing",
}


def validate_idea_evidence_materialization_contract(
    contract_path: Path = CONTRACT_PATH,
) -> list[str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for key, expected in REQUIRED_TOP_LEVEL.items():
        if contract.get(key) != expected:
            errors.append(f"{key} must be {expected!r}")

    authority = contract.get("source_authority")
    if not isinstance(authority, dict):
        errors.append("source_authority must be an object")
    else:
        expected_authority = {
            "idea_evidence": "lotus-idea",
            "report_materialization": "lotus-report",
            "rendering": "lotus-render",
            "archive_record": "lotus-archive",
            "client_publication": "blocked",
        }
        for key, expected in expected_authority.items():
            if authority.get(key) != expected:
                errors.append(f"source_authority.{key} must be {expected}")

    required_fields = set(contract.get("required_fields", ()))
    missing_fields = REQUIRED_FIELDS - required_fields
    if missing_fields:
        errors.append("required_fields missing: " + ", ".join(sorted(missing_fields)))

    nested = contract.get("required_nested_fields")
    nested_fields = set(nested.get("idea_evidence_pack", ())) if isinstance(nested, dict) else set()
    missing_nested = REQUIRED_NESTED_IDEA_FIELDS - nested_fields
    if missing_nested:
        errors.append(
            "required_nested_fields.idea_evidence_pack missing: "
            + ", ".join(sorted(missing_nested))
        )

    response_fields = set(contract.get("response_fields", ()))
    missing_response_fields = REQUIRED_RESPONSE_FIELDS - response_fields
    if missing_response_fields:
        errors.append("response_fields missing: " + ", ".join(sorted(missing_response_fields)))

    nested_response = contract.get("required_nested_response_fields")
    package_identity_fields = (
        set(nested_response.get("report_package_identity", ()))
        if isinstance(nested_response, dict)
        else set()
    )
    missing_package_identity = REQUIRED_REPORT_PACKAGE_IDENTITY_FIELDS - package_identity_fields
    if missing_package_identity:
        errors.append(
            "required_nested_response_fields.report_package_identity missing: "
            + ", ".join(sorted(missing_package_identity))
        )

    blockers = set(contract.get("certification_blockers", ()))
    if blockers != REMAINING_BLOCKERS:
        errors.append(
            "certification_blockers must retain only: " + ", ".join(sorted(REMAINING_BLOCKERS))
        )

    boundaries = " ".join(str(item) for item in contract.get("non_proof_boundaries", ()))
    for required_fragment in (
        "Does not grant suitability",
        "Does not recompute lotus-idea evidence",
        "Does not promote a supported feature",
    ):
        if required_fragment not in boundaries:
            errors.append(f"non_proof_boundaries must mention {required_fragment}")

    for ref in contract.get("evidence_refs", ()):
        if not isinstance(ref, str) or not ref.strip():
            errors.append("evidence_refs must contain non-empty strings")
            continue
        path = ROOT / ref
        if not path.exists():
            errors.append(f"evidence ref path missing: {ref}")
    return errors


def main() -> int:
    errors = validate_idea_evidence_materialization_contract()
    if errors:
        print("\n".join(errors))
        return 1
    print("Idea evidence materialization contract gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
