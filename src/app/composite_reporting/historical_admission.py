"""Exact normalized root/correction custody, never current policy authorization."""

import calendar
from typing import Any

from app.composite_reporting.admission import response_digest
from app.composite_reporting.amendment_admission import (
    require_link,
    require_projected_decisions,
)
from app.composite_reporting.eligibility_admission import (
    _require_proposal,
    _require_scope,
    require_hash,
    require_source_months,
)
from app.composite_reporting.eligibility_contract import proposal_for_month
from app.composite_reporting.historical_source import (
    instant,
    require,
    require_operation,
    validate_product,
)
from app.composite_reporting.models import (
    EligibilitySelection,
    HistoricalEligibilitySelection,
    PublishedEligibilityPin,
)


def require_retained_receipt(
    selection: HistoricalEligibilitySelection, receipt: dict[str, Any]
) -> None:
    validate_product(receipt)
    approval = receipt["approval"]
    proposal = approval["proposal"]
    version = receipt["product_version"]
    require(version == approval["product_version"] == proposal["product_version"])
    validate_product(approval)
    require(approval["approved_by"] != proposal["proposed_by"])
    require(instant(approval["approved_at"]) >= instant(proposal["proposed_at"]))
    require_operation(approval, proposal, "EVALUATION_APPROVAL")
    definition = receipt["definition"]
    require(
        definition.get("product_name") == "CompositeDefinition"
        and definition.get("product_version") in {"v1", "v2"}
    )
    _require_scope(selection, definition)
    require(definition["reporting_currency"] == selection.reporting_currency)
    require_hash(definition, recursive=definition["product_version"] == "v1")
    month = proposal["evaluation"]["month"]
    first = month + "-01"
    last = month + f"-{calendar.monthrange(int(month[:4]), int(month[5:]))[1]:02d}"
    prior = EligibilitySelection.model_validate(
        {
            **selection.model_dump(mode="json", exclude={"selection_version", "months"}),
            "period_start": first,
            "period_end": last,
            "months": [
                {
                    "evidence_kind": "EVALUATED_ONLY",
                    "month": month,
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
    _require_proposal(prior, 0, proposal, source_version=version)
    for key, name, revision, digest in [
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
    ]:
        require(
            receipt[key]
            == {
                "product_name": name,
                "product_version": "v1",
                "revision": revision,
                "digest": digest,
            }
        )
    require(receipt["source_cut_id"] == proposal["universe"]["source_cut_id"])
    if version == "v4":
        require(receipt["lineage"] == proposal["amendment"])
        require(
            receipt["publication_sequence"]
            == proposal["amendment"]["expected_current_publication_sequence"] + 1
        )


def require_months(selection: HistoricalEligibilitySelection, months: list[dict[str, Any]]) -> None:
    require(len(selection.months) == len(months))
    for pin, month in zip(selection.months, months, strict=True):
        single = selection.model_copy(update={"months": [pin]})
        require_source_months(single, [month], source_version=pin.product_version)
        proposal, _ = proposal_for_month(month)
        receipts = month["lineage_receipts"]
        if pin.product_version == "v3":
            require(not receipts and month.get("parent_publication") is None)
        else:
            expected = pin.lineage_receipts
            require(len(receipts) == len(expected))
            original = receipts[-1]
            require(original["product_version"] == "v3")
            seen = {proposal["evaluation_revision"]}
            current = proposal
            for retained, receipt in zip(expected, receipts, strict=True):
                require_retained_receipt(selection, receipt)
                approval = receipt["approval"]
                revision = approval["proposal"]["evaluation_revision"]
                require(revision not in seen)
                seen.add(revision)
                require(
                    (
                        receipt["product_version"],
                        revision,
                        approval["content_hash"],
                        receipt["content_hash"],
                        response_digest(receipt),
                    )
                    == (
                        retained.product_version,
                        retained.evaluation_revision,
                        retained.approval_content_hash,
                        retained.receipt_content_hash,
                        retained.receipt_response_digest,
                    )
                )
                require(current["product_version"] == "v4")
                require_link(current, receipt, original, source_version="v4")
                current = receipt["approval"]["proposal"]
            require(current["product_version"] == "v3")
            if isinstance(pin, PublishedEligibilityPin):
                parent = month["parent_publication"]
                _require_scope(selection, parent)
                require(
                    parent["sequence"]
                    == proposal["amendment"]["expected_current_publication_sequence"]
                )
                require(
                    (parent["membership_revision"], parent["membership_content_hash"])
                    == (pin.parent_membership_revision, pin.parent_membership_content_hash)
                )
                require(
                    all(row["definition"] == month["receipt"]["definition"] for row in receipts)
                )
                require_projected_decisions(month, proposal)
        if isinstance(pin, PublishedEligibilityPin):
            require_retained_receipt(selection, month["receipt"])
