"""Versioned examples exported from qualified Manage domain fixtures, without IO."""

import json
from copy import deepcopy
from pathlib import Path

from app.composite_reporting.admission import response_digest
from app.composite_reporting.models import AmendmentEligibilitySelection


def example(definition_version="v1", revision=3, evaluated=False):
    root = next(
        parent for parent in Path(__file__).resolve().parents if (parent / "contracts").is_dir()
    )
    bundle = json.loads(
        (
            root
            / "contracts/examples"
            / (f"composite-monthly-amendment.v6.definition-{definition_version}.source.json")
        ).read_text()
    )
    month = deepcopy(bundle["months"][revision - 2])
    receipt = month["receipt"]
    proposal = receipt["approval"]["proposal"]
    evaluation = proposal["evaluation"]
    pin = {
        "evidence_kind": "PUBLISHED",
        "month": evaluation["month"],
        "evaluation_revision": proposal["evaluation_revision"],
        "proposal_content_hash": proposal["content_hash"],
        "approval_content_hash": receipt["approval"]["content_hash"],
        "receipt_content_hash": receipt["content_hash"],
        "membership_revision": month["membership"]["membership_revision"],
        "membership_content_hash": month["membership"]["content_hash"],
        "attestation_version": month["universe"]["attestation_version"],
        "universe_content_hash": month["universe"]["content_hash"],
        "parent_membership_revision": proposal["parent_membership_revision"],
        "parent_membership_content_hash": proposal["parent_membership_content_hash"],
        "publication_sequence": receipt["publication_sequence"],
        "source_cut_id": proposal["observations"]["source_cut_id"],
        "lineage_receipts": [
            {
                "product_version": prior["product_version"],
                "evaluation_revision": prior["approval"]["proposal"]["evaluation_revision"],
                "approval_content_hash": prior["approval"]["content_hash"],
                "receipt_content_hash": prior["content_hash"],
                "receipt_response_digest": response_digest(prior),
            }
            for prior in month["lineage_receipts"]
        ],
    }
    if evaluated:
        pin = {
            key: value
            for key, value in pin.items()
            if key
            in (
                "month",
                "evaluation_revision",
                "proposal_content_hash",
                "parent_membership_revision",
                "parent_membership_content_hash",
                "source_cut_id",
                "lineage_receipts",
            )
        }
        pin["evidence_kind"] = "EVALUATED_ONLY"
        month = {
            "evidence_kind": "EVALUATED_ONLY",
            "proposal": proposal,
            "lineage_receipts": month["lineage_receipts"],
        }
    month["response_digests"] = {
        key: response_digest(value) for key, value in month.items() if isinstance(value, dict)
    }
    for key, digest in month["response_digests"].items():
        pin[("parent" if key == "parent_membership" else key) + "_response_digest"] = digest
    policy = proposal["policy_approval"]["proposal"]["policy"]
    selection = AmendmentEligibilitySelection.model_validate(
        {
            **{
                key: policy["scope"][key]
                for key in ("tenant_id", "composite_id", "definition_version")
            },
            "selection_version": "v2",
            "reporting_currency": proposal["observations"]["reporting_currency"],
            "period_start": proposal["amendment"]["affected_from"],
            "period_end": proposal["amendment"]["affected_to"],
            "months": [pin],
        }
    )
    return selection, [month]
