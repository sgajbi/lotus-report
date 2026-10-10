"""Exact retained correction graph custody, without source rule recomputation."""

import calendar
from datetime import date, datetime
from typing import Any

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.amendment_contract import AmendmentProposal, AmendmentReceipt
from app.composite_reporting.eligibility_admission import (
    _require_proposal,
    _require_scope,
    require_hash,
    require_source_months,
)
from app.composite_reporting.eligibility_contract import EligibilityReceipt, proposal_for_month
from app.composite_reporting.models import AmendmentEligibilitySelection, EligibilitySelection


def refuse(condition: bool, code: str) -> None:
    if condition:
        raise CompositeEvidenceRefused("COMPOSITE_AMENDMENT_" + code)


def approval_binding(approval: dict[str, Any]) -> dict[str, str]:
    return {
        "product_name": "CompositeMonthlyEvaluationApproval",
        "product_version": approval["product_version"],
        "revision": approval["proposal"]["evaluation_revision"],
        "digest": approval["content_hash"],
    }


def receipt_binding(receipt: dict[str, Any]) -> dict[str, str]:
    return {
        "product_name": "CompositeMonthlyEligibilityPublicationReceipt",
        "product_version": receipt["product_version"],
        "revision": receipt["approval"]["proposal"]["evaluation_revision"],
        "digest": receipt["content_hash"],
    }


def require_receipt(selection: AmendmentEligibilitySelection, receipt: dict[str, Any]) -> None:
    version = receipt.get("product_version")
    model = AmendmentReceipt if version == "v2" else EligibilityReceipt
    model.model_validate(receipt)
    approval = receipt["approval"]
    proposal = approval["proposal"]
    definition = receipt["definition"]
    refuse(
        definition.get("product_name") != "CompositeDefinition"
        or definition.get("product_version") not in ("v1", "v2"),
        "DEFINITION_VERSION_CONFLICT",
    )
    _require_scope(selection, definition)
    refuse(definition["reporting_currency"] != selection.reporting_currency, "CURRENCY_CONFLICT")
    require_hash(definition, recursive=definition["product_version"] == "v1")
    for product in (receipt, approval, proposal):
        require_hash(product)
    refuse(receipt["source_cut_id"] != proposal["universe"]["source_cut_id"], "SOURCE_CUT_CONFLICT")
    for key, name, revision, digest in (
        (
            "membership_binding",
            "CompositeMembership",
            proposal["target_membership_revision"],
            approval["membership_content_hash"],
        ),
        (
            "universe_binding",
            "CompositeUniverseAttestation",
            proposal["evaluation_revision"],
            approval["published_universe_content_hash"],
        ),
    ):
        refuse(
            receipt[key]
            != {
                "product_name": name,
                "product_version": "v1",
                "revision": revision,
                "digest": digest,
            },
            "RECEIPT_BINDING_CONFLICT",
        )
    # Reuse structural population/policy/observation checks with exact prior pins.
    prior = EligibilitySelection.model_validate(
        {
            **selection.model_dump(mode="json", exclude={"selection_version", "months"}),
            "period_start": proposal["evaluation"]["month"] + "-01",
            "period_end": proposal["evaluation"]["month"]
            + "-"
            + str(
                calendar.monthrange(
                    int(proposal["evaluation"]["month"][:4]),
                    int(proposal["evaluation"]["month"][5:]),
                )[1]
            ),
            "months": [
                {
                    "evidence_kind": "EVALUATED_ONLY",
                    "month": proposal["evaluation"]["month"],
                    "evaluation_revision": proposal["evaluation_revision"],
                    "proposal_content_hash": proposal["content_hash"],
                    "proposal_response_digest": response_digest(proposal),
                    "source_cut_id": proposal["observations"]["source_cut_id"],
                    "parent_membership_revision": proposal["parent_membership_revision"],
                    "parent_membership_content_hash": proposal["parent_membership_content_hash"],
                }
            ],
        }
    )
    _require_proposal(prior, 0, proposal, source_version=str(version))
    refuse(
        proposal.get("publication_evidence_version") != "v1"
        or not proposal.get("source_assembly_evidence"),
        "PUBLICATION_EVIDENCE_UNAVAILABLE",
    )
    if version == "v2":
        refuse(receipt["lineage"] != proposal["amendment"], "RECEIPT_LINEAGE_CONFLICT")
        refuse(
            receipt["publication_sequence"]
            <= proposal["amendment"]["expected_current_publication_sequence"],
            "PUBLICATION_SEQUENCE_CONFLICT",
        )


def require_link(
    proposal: dict[str, Any],
    predecessor: dict[str, Any],
    original: dict[str, Any],
    *,
    source_version: str = "v2",
) -> None:
    if source_version == "v4":
        from app.composite_reporting.historical_source import validate_product

        validate_product(proposal)
    else:
        AmendmentProposal.model_validate(proposal)
    claim = proposal["amendment"]
    prior = predecessor["approval"]["proposal"]
    refuse(
        claim["predecessor_approval_binding"] != approval_binding(predecessor["approval"])
        or claim["expected_authority_binding"] != claim["predecessor_approval_binding"]
        or claim["predecessor_receipt_binding"] != receipt_binding(predecessor)
        or claim["original_approval_binding"] != approval_binding(original["approval"]),
        "AUTHORITY_BINDING_CONFLICT",
    )
    refuse(
        proposal["evaluation_revision"]
        in {prior["evaluation_revision"], original["approval"]["proposal"]["evaluation_revision"]},
        "REVISION_REUSED",
    )
    month = proposal["evaluation"]["month"]
    first = date.fromisoformat(month + "-01")
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    refuse(
        (claim["affected_from"], claim["affected_to"]) != (first.isoformat(), last.isoformat())
        or prior["evaluation"]["month"] != month,
        "WINDOW_CONFLICT",
    )
    refuse(
        claim["projection_parent_membership_binding"]
        != {
            "product_name": "CompositeMembership",
            "product_version": "v1",
            "revision": proposal["parent_membership_revision"],
            "digest": proposal["parent_membership_content_hash"],
        },
        "PARENT_BINDING_CONFLICT",
    )
    refuse(
        claim["projection_parent_membership_binding"] != predecessor["membership_binding"]
        or claim["expected_current_publication_sequence"] != predecessor["publication_sequence"],
        "CASCADE_UNSUPPORTED",
    )
    refuse(not claim["reason"].strip(), "REASON_REQUIRED")
    identities = [
        (item["product_name"], item["product_version"], item["revision"])
        for item in claim["evidence_bindings"]
    ]
    refuse(len(set(identities)) != len(identities), "AMBIGUOUS_EVIDENCE")
    refuse(proposal["policy_approval"] != prior["policy_approval"], "POLICY_CHANGE_UNSUPPORTED")
    refuse(
        proposal["universe"]["expected_portfolio_ids"]
        != prior["universe"]["expected_portfolio_ids"],
        "POPULATION_CHANGE_UNSUPPORTED",
    )
    old, new = prior["observations"], proposal["observations"]
    refuse(
        all(old[key] == new[key] for key in ("source_cut_id", "source_revision", "portfolios")),
        "SOURCE_UNCHANGED",
    )
    refuse(
        datetime.fromisoformat(proposal["proposed_at"])
        < datetime.fromisoformat(predecessor["approval"]["approved_at"]),
        "CLOCK_CONFLICT",
    )


def require_amendment_months(
    selection: AmendmentEligibilitySelection, months: list[dict[str, Any]]
) -> None:
    require_source_months(selection, months, source_version="v2")
    for pin, month in zip(selection.months, months, strict=True):
        proposal, _ = proposal_for_month(month)
        receipts = month["lineage_receipts"]
        refuse(len(receipts) != len(pin.lineage_receipts), "LINEAGE_POPULATION_CONFLICT")
        seen = {proposal["evaluation_revision"]}
        for expected, receipt in zip(pin.lineage_receipts, receipts, strict=True):
            require_receipt(selection, receipt)
            approval = receipt["approval"]
            revision = approval["proposal"]["evaluation_revision"]
            refuse(revision in seen, "LINEAGE_CYCLE")
            seen.add(revision)
            refuse(
                (
                    receipt["product_version"],
                    revision,
                    approval["content_hash"],
                    receipt["content_hash"],
                    response_digest(receipt),
                )
                != (
                    expected.product_version,
                    expected.evaluation_revision,
                    expected.approval_content_hash,
                    expected.receipt_content_hash,
                    expected.receipt_response_digest,
                ),
                "LINEAGE_PIN_CONFLICT",
            )
        original = receipts[-1]
        refuse(original["product_version"] != "v1", "ORDINARY_ROOT_REQUIRED")
        current = proposal
        for receipt in receipts:
            require_link(current, receipt, original)
            current = receipt["approval"]["proposal"]
        refuse(current["product_version"] != "v1", "INCOMPLETE_LINEAGE")
        if month["evidence_kind"] == "PUBLISHED":
            require_receipt(selection, month["receipt"])
            refuse(
                any(item["definition"] != month["receipt"]["definition"] for item in receipts),
                "DEFINITION_CONFLICT",
            )
            parent = month["parent_membership"]
            publication = month["parent_publication"]
            _require_scope(selection, publication)
            refuse(
                (
                    publication["sequence"],
                    publication["membership_revision"],
                    publication["membership_content_hash"],
                )
                != (
                    proposal["amendment"]["expected_current_publication_sequence"],
                    parent["membership_revision"],
                    parent["content_hash"],
                ),
                "PARENT_PUBLICATION_CONFLICT",
            )
            refuse(
                month["receipt"]["publication_sequence"] <= publication["sequence"],
                "PUBLICATION_SEQUENCE_CONFLICT",
            )
            require_projected_decisions(month, proposal)


def require_projected_decisions(month: dict[str, Any], proposal: dict[str, Any]) -> None:
    """Compare source-stated reasons and observation facts, without evaluating rules."""
    evaluation = proposal["evaluation"]
    observations = {row["portfolio_id"]: row for row in proposal["observations"]["portfolios"]}
    by_id = {
        row["portfolio_id"]: row
        for row in month["membership"]["decisions"]
        if row["effective_from"] == proposal["amendment"]["affected_from"]
        and row["effective_to"] == proposal["amendment"]["affected_to"]
    }
    for result in evaluation["portfolios"]:
        reasons = [
            reason
            for assessment in result["assessments"]
            for reason in (
                assessment["failure_reasons"]
                if result["status"] == "EXCLUDED"
                else assessment["unknown_reasons"]
            )
        ]
        expected_reason = None if result["status"] == "INCLUDED" else next(iter(reasons), None)
        decision = by_id.get(result["portfolio_id"])
        refuse(
            decision is None
            or (
                decision["status"],
                decision["reason_code"],
                decision["discretionary"],
                decision["approval_ref"],
                decision["source_snapshot_id"],
            )
            != (
                result["status"],
                expected_reason,
                observations[result["portfolio_id"]]["discretionary"],
                month["receipt"]["approval"]["claims_digest"],
                evaluation["content_hash"],
            ),
            "PROJECTED_DECISION_CONFLICT",
        )
