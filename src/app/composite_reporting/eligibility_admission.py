"""Structural source custody admission; no source economic or approval evaluation."""

from __future__ import annotations

import calendar
import json
from collections import Counter
from datetime import date
from hashlib import sha256
from typing import Any

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.eligibility_contract import (
    EligibilityMembership,
    EligibilityObservations,
    EligibilityPolicy,
    EligibilityProposal,
    EligibilityPublication,
    EligibilityReceipt,
    EligibilityUniverse,
    EvaluatedSourceMonth,
    PublishedSourceMonth,
    proposal_for_month,
)
from app.composite_reporting.models import (
    AmendmentEligibilitySelection,
    EligibilitySelection,
    PublishedEligibilityPin,
)

MonthlySelection = EligibilitySelection | AmendmentEligibilitySelection


def source_hash(payload: dict[str, Any], *, recursive: bool = False) -> str:
    """Manage canonical product hash; distinct from Report capture response digest."""

    def stripped(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: stripped(item) for key, item in value.items() if key != "content_hash"}
        if isinstance(value, list):
            return [stripped(item) for item in value]
        return value

    content = (
        stripped(payload)
        if recursive
        else {key: value for key, value in payload.items() if key != "content_hash"}
    )
    wire = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + sha256(wire.encode("utf-8")).hexdigest()


def require_hash(payload: dict[str, Any], *, recursive: bool = False) -> None:
    if payload.get("content_hash") != source_hash(payload, recursive=recursive):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_SOURCE_HASH_CONFLICT")


def _require_scope(selection: MonthlySelection, payload: dict[str, Any]) -> None:
    if any(
        payload.get(key) != getattr(selection, key)
        for key in ("tenant_id", "composite_id", "definition_version")
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_SOURCE_SCOPE_CONFLICT")


def _require_population(proposal: dict[str, Any], *, source_version: str = "v1") -> None:
    if source_version == "v2":
        from app.composite_reporting.amendment_contract import AmendmentProposal

        evaluation = AmendmentProposal.model_validate(proposal).evaluation
    else:
        evaluation = EligibilityProposal.model_validate(proposal).evaluation
    ids = [row.portfolio_id for row in evaluation.portfolios]
    expected = proposal["universe"]["expected_portfolio_ids"]
    observations = proposal["observations"]["portfolios"]
    observed = {row["portfolio_id"] for row in observations}
    by_id: dict[str, dict[str, Any]] = {}
    for row in observations:
        if row["portfolio_id"] in by_id and by_id[row["portfolio_id"]] != row:
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_OBSERVATION_DUPLICATE_CONFLICT")
        by_id[row["portfolio_id"]] = row
    if not observed.issubset(set(expected)) or any(
        row.observations_present != (row.portfolio_id in observed) for row in evaluation.portfolios
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_OBSERVATION_POPULATION_CONFLICT")
    if (
        ids != sorted(set(ids))
        or ids != expected
        or ids != (proposal["observations"]["expected_portfolio_ids"])
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_POPULATION_CONFLICT")
    statuses = Counter(row.status for row in evaluation.portfolios)
    counts = {
        "expected_count": len(ids),
        "observed_count": sum(row.observations_present for row in evaluation.portfolios),
        "included_count": statuses["INCLUDED"],
        "excluded_count": statuses["EXCLUDED"],
        "pending_review_count": statuses["PENDING_REVIEW"],
    }
    if any(getattr(evaluation, key) != value for key, value in counts.items()):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_POPULATION_COUNT_CONFLICT")
    for row in evaluation.portfolios:
        if [item.rule for item in row.assessments] != ["SIGNIFICANT_FLOW", "CASH", "READINESS"]:
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_ASSESSMENT_SET_CONFLICT")


def _require_proposal(
    selection: MonthlySelection,
    index: int,
    proposal: dict[str, Any],
    *,
    source_version: str = "v1",
) -> None:
    pin = selection.months[index]
    _require_population(proposal, source_version=source_version)
    EligibilityObservations.model_validate(proposal["observations"])
    require_hash(proposal)
    evaluation, universe, observations = (
        proposal["evaluation"],
        proposal["universe"],
        proposal["observations"],
    )
    for product in (evaluation, universe, observations):
        _require_scope(selection, product)
    for product in (evaluation, observations):
        if product.get("source_cut_id") != pin.source_cut_id:
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_SOURCE_CUT_CONFLICT")
    require_hash(evaluation)
    require_hash(universe, recursive=True)
    EligibilityUniverse.model_validate(universe)
    _require_policy(selection, pin.month, proposal)
    first = date.fromisoformat(pin.month + "-01")
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    if (
        date.fromisoformat(universe["coverage_from"]) > first
        or date.fromisoformat(universe["coverage_to"]) < last
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_UNIVERSE_WINDOW_CONFLICT")
    if (
        any(
            proposal[key] != getattr(pin, key)
            for key in (
                "evaluation_revision",
                "parent_membership_revision",
                "parent_membership_content_hash",
            )
        )
        or proposal["content_hash"] != pin.proposal_content_hash
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PROPOSAL_PIN_CONFLICT")
    if (
        evaluation["month"] != pin.month
        or observations["month"] != pin.month
        or (observations["reporting_currency"] != selection.reporting_currency)
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_MONTH_CURRENCY_CONFLICT")
    if (
        (universe["membership_revision"], universe["membership_content_hash"])
        != (pin.parent_membership_revision, pin.parent_membership_content_hash)
        or evaluation["universe_content_hash"] != universe["content_hash"]
        or (evaluation["input_content_hash"] != source_hash({**observations, "content_hash": ""}))
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_INPUT_BINDING_CONFLICT")


def _require_policy(selection: MonthlySelection, month: str, proposal: dict[str, Any]) -> None:
    approval = proposal["policy_approval"]
    policy_proposal = approval["proposal"]
    policy = policy_proposal["policy"]
    EligibilityPolicy.model_validate(policy)
    for product in (approval, policy_proposal, policy):
        require_hash(product)
    _require_scope(selection, policy["scope"])
    if (
        approval.get("product_name") != "CompositeMonthlyPolicyApproval"
        or approval.get("evidence_kind") != "SYNTHETIC_UNSIGNED"
        or approval.get("official_activation") != "UNAVAILABLE"
        or policy.get("month") != month
        or policy != proposal["evaluation"]["resolved_policy"]
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_POLICY_BINDING_CONFLICT")
    if any(product.get("product_version") != "v1" for product in (approval, policy_proposal)):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_POLICY_BINDING_CONFLICT")
    products = [
        item
        for item in proposal["universe"]["source_products"]
        if (
            item.get("product_name") == "CompositeMonthlyEligibilityObservations"
            and item.get("contract_version") == "v1"
            and item.get("authority_scope") == "POLICY_INPUT"
        )
    ]
    observations = proposal["observations"]
    if len(products) != 1 or tuple(
        products[0].get(key) for key in ("source_cut_id", "source_watermark", "content_hash")
    ) != (
        observations["source_cut_id"],
        observations["source_revision"],
        source_hash(observations),
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_OBSERVATION_LOCATOR_CONFLICT")


def require_source_months(
    selection: MonthlySelection, months: list[dict[str, Any]], *, source_version: str = "v1"
) -> None:
    if len(months) != len(selection.months):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_MONTH_POPULATION_CONFLICT")
    for index, (pin, month) in enumerate(zip(selection.months, months, strict=True)):
        if pin.evidence_kind != month.get("evidence_kind"):
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_EVIDENCE_KIND_CONFLICT")
        model = (
            PublishedSourceMonth
            if isinstance(pin, PublishedEligibilityPin)
            else EvaluatedSourceMonth
        )
        if source_version == "v2":
            from app.composite_reporting.amendment_contract import (
                AmendmentEvaluatedMonth,
                AmendmentPublishedMonth,
            )

            amendment_model = (
                AmendmentPublishedMonth
                if isinstance(pin, PublishedEligibilityPin)
                else AmendmentEvaluatedMonth
            )
            amendment_model.model_validate(month)
        else:
            model.model_validate(month)
        proposal, _ = proposal_for_month(month)
        _require_proposal(selection, index, proposal, source_version=source_version)
        expected_keys: tuple[str, ...] = (
            ("receipt", "membership", "universe", "parent_membership", "publication")
            if (isinstance(pin, PublishedEligibilityPin))
            else ("proposal",)
        )
        if source_version == "v2" and isinstance(pin, PublishedEligibilityPin):
            expected_keys += ("parent_publication",)
        if set(month["response_digests"]) != set(expected_keys):
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_CAPTURE_DIGEST_SET_CONFLICT")
        for key in expected_keys:
            pin_key = "parent" if key == "parent_membership" else key
            expected = getattr(pin, f"{pin_key}_response_digest")
            if (
                response_digest(month[key]) != expected
                or month["response_digests"][key] != expected
            ):
                raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_CAPTURE_DIGEST_CONFLICT")
        if isinstance(pin, PublishedEligibilityPin):
            _require_publication(selection, pin, month, source_version=source_version)


def _require_publication(
    selection: MonthlySelection,
    pin: PublishedEligibilityPin,
    month: dict[str, Any],
    *,
    source_version: str = "v1",
) -> None:
    receipt, member, universe, parent = (
        month["receipt"],
        month["membership"],
        month["universe"],
        month["parent_membership"],
    )
    if (
        receipt.get("product_name"),
        receipt.get("product_version"),
        receipt.get("completeness"),
    ) != ("CompositeMonthlyEligibilityPublicationReceipt", source_version, "UNVERIFIED"):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_RECEIPT_PRODUCT_CONFLICT")
    require_hash(receipt)
    if source_version == "v2":
        from app.composite_reporting.amendment_contract import AmendmentReceipt

        AmendmentReceipt.model_validate(receipt)
    else:
        EligibilityReceipt.model_validate(receipt)
    approval = receipt["approval"]
    require_hash(approval)
    if (
        approval.get("product_name"),
        approval.get("product_version"),
        approval.get("evidence_kind"),
        approval.get("official_activation"),
    ) != (
        "CompositeMonthlyEvaluationApproval",
        source_version,
        "SYNTHETIC_UNSIGNED",
        "UNAVAILABLE",
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_APPROVAL_PRODUCT_CONFLICT")
    for product in (member, universe, parent):
        _require_scope(selection, product)
        require_hash(product, recursive=True)
    _require_scope(selection, receipt["definition"])
    require_hash(
        receipt["definition"], recursive=receipt["definition"].get("product_version") == "v1"
    )
    EligibilityMembership.model_validate(member)
    EligibilityMembership.model_validate(parent)
    _require_history_intervals(member)
    _require_history_intervals(parent)
    EligibilityUniverse.model_validate(universe)
    EligibilityPublication.model_validate(month["publication"])
    bindings = (
        (
            "membership_binding",
            "CompositeMembership",
            pin.membership_revision,
            pin.membership_content_hash,
        ),
        (
            "universe_binding",
            "CompositeUniverseAttestation",
            pin.attestation_version,
            pin.universe_content_hash,
        ),
    )
    for key, name, revision, digest in bindings:
        if receipt[key] != {
            "product_name": name,
            "product_version": "v1",
            "revision": revision,
            "digest": digest,
        }:
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PUBLICATION_BINDING_CONFLICT")
    if (
        receipt["content_hash"],
        approval["content_hash"],
        receipt["source_cut_id"],
        receipt["publication_sequence"],
    ) != (
        pin.receipt_content_hash,
        pin.approval_content_hash,
        approval["proposal"]["universe"]["source_cut_id"],
        pin.publication_sequence,
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_RECEIPT_PIN_CONFLICT")
    _require_canonical_products(pin, month, source_version=source_version)
    _require_published_joins(selection, pin, month)


def _require_history_intervals(membership: dict[str, Any]) -> None:
    prior: dict[str, list[tuple[date, date]]] = {}
    for decision in membership["decisions"]:
        start = date.fromisoformat(decision["effective_from"])
        end = (
            date.fromisoformat(decision["effective_to"])
            if decision["effective_to"] is not None
            else date.max
        )
        intervals = prior.setdefault(decision["portfolio_id"], [])
        if end < start or any(
            start <= old_end and old_start <= end for old_start, old_end in intervals
        ):
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_HISTORY_INTERVAL_CONFLICT")
        intervals.append((start, end))


def _require_published_joins(
    selection: MonthlySelection, pin: PublishedEligibilityPin, month: dict[str, Any]
) -> None:
    member, universe, publication, receipt = (
        month["membership"],
        month["universe"],
        month["publication"],
        month["receipt"],
    )
    _require_scope(selection, publication)
    approval = receipt["approval"]
    proposal = approval["proposal"]
    _require_published_population(pin, member, universe, proposal)
    if (
        member["source_cut_id"] != proposal["universe"]["source_cut_id"]
        or universe["source_cut_id"] != member["source_cut_id"]
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PUBLICATION_CUT_CONFLICT")
    if (
        approval["membership_content_hash"] != member["content_hash"]
        or approval["published_universe_content_hash"] != universe["content_hash"]
        or proposal["target_membership_revision"] != member["membership_revision"]
        or member["supersedes_membership_revision"] != pin.parent_membership_revision
        or receipt["definition"]["reporting_currency"] != selection.reporting_currency
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PUBLICATION_JOIN_CONFLICT")
    for key in (
        "membership_revision",
        "membership_content_hash",
        "source_cut_id",
        "policy_version",
        "supersedes_membership_revision",
        "affected_from",
        "affected_to",
        "decided_at",
    ):
        expected = member["content_hash"] if key == "membership_content_hash" else member[key]
        if publication[key] != expected:
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PUBLICATION_JOIN_CONFLICT")
    if publication["sequence"] != pin.publication_sequence or publication["decision_count"] != len(
        member["decisions"]
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PUBLICATION_JOIN_CONFLICT")


def _require_published_population(
    pin: PublishedEligibilityPin,
    member: dict[str, Any],
    universe: dict[str, Any],
    proposal: dict[str, Any],
) -> None:
    evaluated = {row["portfolio_id"]: row["status"] for row in proposal["evaluation"]["portfolios"]}
    first = date.fromisoformat(pin.month + "-01")
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    selected = [
        row
        for row in member["decisions"]
        if (
            date.fromisoformat(row["effective_from"]) <= last
            and (row["effective_to"] is None or date.fromisoformat(row["effective_to"]) >= first)
        )
    ]
    if (
        len(selected) != len(evaluated)
        or {row["portfolio_id"]: row["status"] for row in selected} != evaluated
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PUBLISHED_POPULATION_CONFLICT")
    if universe["expected_portfolio_ids"] != sorted(evaluated) or universe[
        "expected_portfolio_count"
    ] != len(evaluated):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PUBLISHED_POPULATION_CONFLICT")


def _require_canonical_products(
    pin: PublishedEligibilityPin, month: dict[str, Any], *, source_version: str = "v1"
) -> None:
    member, universe, parent, receipt = (
        month["membership"],
        month["universe"],
        month["parent_membership"],
        month["receipt"],
    )
    if (
        member["membership_revision"],
        member["content_hash"],
        parent["membership_revision"],
        parent["content_hash"],
        universe["attestation_version"],
        universe["content_hash"],
        universe["membership_revision"],
        universe["membership_content_hash"],
    ) != (
        pin.membership_revision,
        pin.membership_content_hash,
        pin.parent_membership_revision,
        pin.parent_membership_content_hash,
        pin.attestation_version,
        pin.universe_content_hash,
        pin.membership_revision,
        pin.membership_content_hash,
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_CANONICAL_PIN_CONFLICT")
    proposal = receipt["approval"]["proposal"]
    if proposal.get("publication_evidence_version") != "v1" or not proposal.get(
        "source_assembly_evidence"
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_PUBLICATION_EVIDENCE_UNAVAILABLE")
    locators = [
        item
        for item in universe["source_products"]
        if (
            item.get("owner_service") == "lotus-manage"
            and item.get("product_name") == "CompositeMonthlyEvaluationApproval"
        )
    ]
    expected = (source_version, pin.evaluation_revision, pin.approval_content_hash)
    if (
        len(locators) != 1
        or tuple(
            locators[0].get(key) for key in ("contract_version", "source_watermark", "content_hash")
        )
        != expected
    ):
        raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_APPROVAL_LOCATOR_CONFLICT")
