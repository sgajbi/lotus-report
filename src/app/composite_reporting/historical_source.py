"""Pinned normalized monthly evidence; source trust remains owned by Manage."""

import base64
import json
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.eligibility_admission import require_hash, source_hash


def require(condition: bool) -> None:
    if not condition:
        raise CompositeEvidenceRefused("COMPOSITE_HISTORICAL_SOURCE_BINDING_CONFLICT")


@lru_cache
def product_schema(name: str, version: str) -> dict[str, Any]:
    allowed = {
        "CompositeMonthlyPolicyProposal": {"v2"},
        "CompositeMonthlyPolicyApproval": {"v2"},
        "CompositeMonthlyEvaluationProposal": {"v3", "v4"},
        "CompositeMonthlyEvaluationApproval": {"v3", "v4"},
        "CompositeMonthlyEligibilityPublicationReceipt": {"v3", "v4"},
    }
    require(version in allowed.get(name, set()))
    schema: dict[str, Any] = json.loads(
        (
            Path(__file__).with_name("historical_schemas") / f"{name}.{version}.schema.json"
        ).read_bytes()
    )
    return schema


def validate_product(product: dict[str, Any]) -> None:
    schema = product_schema(product.get("product_name", ""), product.get("product_version", ""))
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    if next(validator.iter_errors(product), None) is not None:
        raise CompositeEvidenceRefused("COMPOSITE_HISTORICAL_SOURCE_SCHEMA_CONFLICT")
    require_hash(product)


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None)
    return result


def require_proof(
    proof: dict[str, Any],
    policy_proposal: dict[str, Any],
    *,
    operation: str,
    actor: str,
    revision: str,
    at: str,
    intent: str,
) -> None:
    """Bind recorded normalized proof to this operation, not a fresh current grant."""
    require_hash(proof)
    mapping = proof["mapping"]
    require_hash(mapping)
    reference = mapping["reference"]
    original = base64.b64decode(mapping["raw_original_base64"], validate=True)
    require("sha256:" + sha256(original).hexdigest() == reference["raw_digest"])
    request = proof["request"]
    require(
        request
        == {
            "product_name": "CompositeHistoricalPolicyVerificationRequest",
            "product_version": "v1",
            "purpose": "COMPOSITE_HISTORICAL_MONTHLY_POLICY_ADMISSION",
            "reference": reference,
            "scope": policy_proposal["policy"]["scope"],
            "month": policy_proposal["policy"]["month"],
            "eligibility_policy_version": policy_proposal["eligibility_policy_version"],
            "reporting_currency": mapping["reporting_currency"],
            "operation": operation,
            "revision": revision,
            "actor_id": actor,
            "requested_at": at,
            "intent_digest": intent,
        }
    )
    require(mapping == policy_proposal["verification"]["mapping"])
    require(mapping["policy"] == policy_proposal["policy"])
    require(mapping["attachments"] == policy_proposal["attachments"])
    require(mapping["eligibility_policy_version"] == policy_proposal["eligibility_policy_version"])
    checked, admitted, expiry = (
        instant(proof[key]) for key in ("checked_at", "admitted_at", "expires_at")
    )
    requested = instant(at)
    require(checked <= requested <= admitted < expiry and (expiry - checked).total_seconds() <= 300)
    require(proof["original_signature_status"] == "VERIFIED_AT_ORIGINAL_APPROVAL")
    require(proof["current_revocation_status"] == "CLEAR")
    require(proof["signer_principal_id"] != proof["verifier_principal_id"])
    require(proof["signer_key_digest"] != proof["verifier_key_digest"])
    public_key = base64.b64decode(proof["verifier_public_key_base64"], validate=True)
    require(
        len(public_key) == 32
        and "sha256:" + sha256(public_key).hexdigest() == proof["verifier_key_digest"]
    )
    require(len(base64.b64decode(proof["verifier_credential"], validate=True)) == 64)


def require_policy(selection: Any, month: str, proposal: dict[str, Any]) -> None:
    approval = proposal["policy_approval"]
    validate_product(approval)
    policy_proposal = approval["proposal"]
    validate_product(policy_proposal)
    policy = policy_proposal["policy"]
    from app.composite_reporting.eligibility_contract import EligibilityPolicy

    EligibilityPolicy.model_validate(policy)
    require_hash(policy)
    require(policy == proposal["evaluation"]["resolved_policy"])
    require(policy["month"] == month)
    require(
        all(
            policy["scope"][key] == getattr(selection, key)
            for key in ("tenant_id", "composite_id", "definition_version")
        )
    )
    require(approval["approved_by"] != policy_proposal["proposed_by"])
    require(instant(approval["approved_at"]) >= instant(policy_proposal["proposed_at"]))
    require(instant(approval["approved_at"]) <= instant(proposal["proposed_at"]))
    mapping = policy_proposal["verification"]["mapping"]
    require(mapping["reporting_currency"] == selection.reporting_currency)
    require(mapping["original_proposed_by"] != mapping["original_approved_by"])
    require(
        instant(mapping["original_proposed_at"])
        <= instant(mapping["original_approved_at"])
        < instant(month + "-01T00:00:00Z")
    )
    intent = source_hash(
        {
            "reference": mapping["reference"],
            "proposal_revision": policy_proposal["proposal_revision"],
        }
    )
    require_proof(
        policy_proposal["verification"],
        policy_proposal,
        operation="POLICY_PROPOSAL",
        actor=policy_proposal["proposed_by"],
        revision=policy_proposal["proposal_revision"],
        at=policy_proposal["proposed_at"],
        intent=intent,
    )
    require_proof(
        approval["verification"],
        policy_proposal,
        operation="POLICY_APPROVAL",
        actor=approval["approved_by"],
        revision=policy_proposal["proposal_revision"],
        at=approval["approved_at"],
        intent=policy_proposal["content_hash"],
    )
    require_operation(proposal, proposal, "EVALUATION_PROPOSAL")


def require_operation(product: dict[str, Any], proposal: dict[str, Any], operation: str) -> None:
    approval = operation == "EVALUATION_APPROVAL"
    intent = (
        proposal["content_hash"]
        if approval
        else source_hash(
            {
                key: value
                for key, value in proposal.items()
                if key not in {"content_hash", "operation_verification"}
            }
        )
    )
    require_proof(
        product["operation_verification"],
        proposal["policy_approval"]["proposal"],
        operation=operation,
        actor=product["approved_by" if approval else "proposed_by"],
        revision=proposal["evaluation_revision"],
        at=product["approved_at" if approval else "proposed_at"],
        intent=intent,
    )
