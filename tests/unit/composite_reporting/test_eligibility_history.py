"""Unapproved in-memory transport examples; no producer publication is manufactured."""

import calendar
from copy import deepcopy
from datetime import date

import pytest

from app.composite_reporting.admission import response_digest
from app.composite_reporting.eligibility_tables import build_eligibility_dataset
from app.composite_reporting.models import EligibilitySelection
from tests.unit.composite_reporting.test_eligibility_contract import evaluated_example, seal


def published_example(
    month="2026-07", parent=None, status="EXCLUDED", outcomes=None, universe_cut="cut-1"
):
    selection, evaluated = evaluated_example()
    proposal = deepcopy(evaluated[0]["proposal"])
    scope = {
        key: getattr(selection, key) for key in ("tenant_id", "composite_id", "definition_version")
    }
    last = month + f"-{calendar.monthrange(int(month[:4]), int(month[-2:]))[1]}"
    first = month + "-01"
    if parent is None:
        parent = seal(
            {
                "product_name": "CompositeMembership",
                "product_version": "v1",
                **scope,
                "membership_revision": "parent-r1",
                "policy_version": "p1",
                "source_cut_id": "prior-cut",
                "decisions": [
                    {
                        "portfolio_id": "excluded",
                        "effective_from": "2026-01-01",
                        "effective_to": "2026-06-30",
                        "status": "INCLUDED",
                        "reason_code": None,
                        "discretionary": True,
                        "approval_ref": None,
                        "source_snapshot_id": "prior-snapshot",
                    }
                ],
                "decided_at": "2026-06-01T00:00:00.000000Z",
                "decided_by": "test-source",
                "correlation_id": "test-correlation",
                "supersedes_membership_revision": None,
                "affected_from": None,
                "affected_to": None,
            },
            recursive=True,
        )
    proposal["evaluation_revision"] = "eval-" + month
    proposal["target_membership_revision"] = "member-" + month
    proposal["parent_membership_revision"] = parent["membership_revision"]
    proposal["parent_membership_content_hash"] = parent["content_hash"]
    proposal["publication_evidence_version"] = "v1"
    proposal["source_assembly_evidence"] = {
        "test_only": "Unapproved unit transport example; not an accepted source assembly"
    }
    policy = proposal["policy_approval"]["proposal"]["policy"]
    policy["month"] = month
    seal(policy)
    seal(proposal["policy_approval"]["proposal"])
    seal(proposal["policy_approval"])
    observation = proposal["observations"]
    observation["month"] = month
    observation["expected_portfolio_ids"] = ["excluded"]
    observation_hash = seal(deepcopy(observation))["content_hash"]
    input_universe = proposal["universe"]
    input_universe["source_cut_id"] = universe_cut
    input_universe.update(
        {
            "membership_revision": parent["membership_revision"],
            "membership_content_hash": parent["content_hash"],
            "expected_portfolio_ids": ["excluded"],
            "expected_portfolio_count": 1,
            "observed_portfolio_count": 1,
            "missing_portfolio_ids": [],
            "posture": "COMPLETE",
            "coverage_from": first,
            "coverage_to": last,
        }
    )
    input_universe["source_products"][0]["content_hash"] = observation_hash
    seal(input_universe, recursive=True)
    evaluation = proposal["evaluation"]
    evaluation.update(
        {
            "month": month,
            "input_content_hash": observation_hash,
            "universe_content_hash": input_universe["content_hash"],
            "resolved_policy": deepcopy(policy),
            "declared_universe_coverage": "COMPLETE",
            "expected_count": 1,
            "observed_count": 1,
            "included_count": int(status == "INCLUDED"),
            "excluded_count": int(status == "EXCLUDED"),
            "pending_review_count": int(status == "PENDING_REVIEW"),
            "portfolios": evaluation["portfolios"][:1],
        }
    )
    evaluation["portfolios"][0]["status"] = status
    if outcomes is not None:
        for assessment, outcome in zip(
            evaluation["portfolios"][0]["assessments"], outcomes, strict=True
        ):
            assessment["outcome"] = outcome
            if outcome == "PASS":
                assessment["failure_reasons"] = []
                assessment["unknown_reasons"] = []
    seal(evaluation)
    seal(proposal)
    decision = {
        "portfolio_id": "excluded",
        "effective_from": first,
        "effective_to": last,
        "status": status,
        "reason_code": next(
            (
                reason
                for assessment in evaluation["portfolios"][0]["assessments"]
                for reason in assessment["failure_reasons"]
            ),
            None,
        )
        if status != "INCLUDED"
        else None,
        "discretionary": True,
        "approval_ref": "test-unapproved-ref",
        "source_snapshot_id": "test-snapshot",
    }
    membership = seal(
        {
            **deepcopy(parent),
            "membership_revision": proposal["target_membership_revision"],
            "source_cut_id": universe_cut,
            "decisions": deepcopy(parent["decisions"]) + [decision],
            "supersedes_membership_revision": parent["membership_revision"],
            "affected_from": first,
            "affected_to": last,
        },
        recursive=True,
    )
    universe = deepcopy(input_universe)
    universe.update(
        {
            "membership_revision": membership["membership_revision"],
            "membership_content_hash": membership["content_hash"],
            "attestation_version": proposal["evaluation_revision"],
        }
    )
    universe["source_products"].append(
        {
            "owner_service": "lotus-manage",
            "product_name": "CompositeMonthlyEvaluationApproval",
            "contract_version": "v1",
            "authority_scope": "POLICY_INPUT",
            "source_cut_id": universe_cut,
            "source_watermark": proposal["evaluation_revision"],
            "content_hash": "sha256:" + "0" * 64,
        }
    )
    seal(universe, recursive=True)
    approval = seal(
        {
            "product_name": "CompositeMonthlyEvaluationApproval",
            "product_version": "v1",
            "evidence_kind": "SYNTHETIC_UNSIGNED",
            "official_activation": "UNAVAILABLE",
            "proposal": proposal,
            "approved_by": "test-checker",
            "approved_at": "2026-10-02T00:00:00.000000Z",
            "claims_digest": "sha256:" + "a" * 64,
            "membership_content_hash": membership["content_hash"],
            "published_universe_content_hash": universe["content_hash"],
        }
    )
    universe["source_products"][-1]["content_hash"] = approval["content_hash"]
    definition = seal(
        {
            "product_name": "CompositeDefinition",
            "product_version": "v1",
            **scope,
            "reporting_currency": "USD",
            "strategy_code": "BALANCED",
            "eligibility_policy_version": "p1",
        },
        recursive=True,
    )
    sequence = int(month[-2:])
    receipt = seal(
        {
            "product_name": "CompositeMonthlyEligibilityPublicationReceipt",
            "product_version": "v1",
            "definition": definition,
            "approval": approval,
            "membership_binding": {
                "product_name": "CompositeMembership",
                "product_version": "v1",
                "revision": membership["membership_revision"],
                "digest": membership["content_hash"],
            },
            "universe_binding": {
                "product_name": "CompositeUniverseAttestation",
                "product_version": "v1",
                "revision": universe["attestation_version"],
                "digest": universe["content_hash"],
            },
            "source_cut_id": universe_cut,
            "publication_sequence": sequence,
            "completeness": "UNVERIFIED",
        }
    )
    publication = {
        "product_name": "CompositeMembershipPublication",
        "product_version": "v1",
        **scope,
        "sequence": sequence,
        "membership_revision": membership["membership_revision"],
        "membership_content_hash": membership["content_hash"],
        "policy_version": "p1",
        "source_cut_id": universe_cut,
        "decision_count": len(membership["decisions"]),
        "supersedes_membership_revision": parent["membership_revision"],
        "affected_from": first,
        "affected_to": last,
        "decided_at": membership["decided_at"],
        "published_at": "2026-10-02T00:00:00.000000Z",
        "completeness": "UNVERIFIED",
    }
    captured = {
        "evidence_kind": "PUBLISHED",
        "receipt": receipt,
        "membership": membership,
        "universe": universe,
        "parent_membership": deepcopy(parent),
        "publication": publication,
    }
    captured["response_digests"] = {
        key: response_digest(value) for key, value in captured.items() if isinstance(value, dict)
    }
    pin = {
        "evidence_kind": "PUBLISHED",
        "month": month,
        "evaluation_revision": proposal["evaluation_revision"],
        "proposal_content_hash": proposal["content_hash"],
        "approval_content_hash": approval["content_hash"],
        "receipt_content_hash": receipt["content_hash"],
        "membership_revision": membership["membership_revision"],
        "membership_content_hash": membership["content_hash"],
        "attestation_version": universe["attestation_version"],
        "universe_content_hash": universe["content_hash"],
        "parent_membership_revision": parent["membership_revision"],
        "parent_membership_content_hash": parent["content_hash"],
        "publication_sequence": sequence,
        "source_cut_id": "cut-1",
    }
    pin.update(
        {
            ("parent" if key == "parent_membership" else key) + "_response_digest": digest
            for key, digest in captured["response_digests"].items()
        }
    )
    return EligibilitySelection.model_validate(
        {
            **scope,
            "reporting_currency": "USD",
            "period_start": first,
            "period_end": last,
            "months": [pin],
        }
    ), [captured]


def test_distinct_monthly_observation_and_retained_universe_cuts_are_preserved():
    selection, months = published_example(universe_cut="retained-universe-cut")
    data = build_eligibility_dataset(selection, months)
    assert data["selection"]["months"][0]["source_cut_id"] == "cut-1"
    assert data["source_months"][0]["receipt"]["source_cut_id"] == "retained-universe-cut"
    assert (
        data["source_months"][0]["receipt"]["approval"]["proposal"]["observations"]["source_cut_id"]
        == "cut-1"
    )


def test_literal_three_month_source_history_keeps_excluded_until_all_source_rules_pass():
    months, pins, parent = [], [], None
    for month, status, outcomes in (
        ("2026-07", "EXCLUDED", ["PASS", "FAIL", "FAIL"]),
        ("2026-08", "EXCLUDED", ["PASS", "PASS", "FAIL"]),
        ("2026-09", "INCLUDED", ["PASS", "PASS", "PASS"]),
    ):
        selection, captured = published_example(month, parent, status, outcomes)
        parent = deepcopy(captured[0]["membership"])
        months.extend(captured)
        pins.extend(selection.months)
    selection = selection.model_copy(update={"period_start": date(2026, 7, 1), "months": pins})
    data = build_eligibility_dataset(selection, months)
    members = data["tables"][1]["rows"]
    assert [row["cells"]["status"]["canonical_value"] for row in members] == [
        "EXCLUDED",
        "EXCLUDED",
        "INCLUDED",
    ]
    history = data["tables"][4]["rows"]
    assert history[0]["cells"]["effective_to"]["canonical_value"] == "2026-06-30"
    assert len(history) == 15  # parent+published revisions: 1+2, 2+3, 3+4
    assert data["source_months"] == months


@pytest.mark.parametrize(
    "key", ["receipt", "membership", "universe", "parent_membership", "publication"]
)
def test_each_whole_published_product_is_pinned(key):
    selection, months = published_example()
    months[0][key]["unexpected_tamper"] = True
    with pytest.raises(ValueError):
        build_eligibility_dataset(selection, months)


def test_proposal_and_published_receipt_cannot_interchange():
    selection, months = published_example()
    months[0]["receipt"] = deepcopy(months[0]["receipt"]["approval"]["proposal"])
    with pytest.raises((ValueError, KeyError)):
        build_eligibility_dataset(selection, months)


def test_known_zero_reasons_is_not_unavailable_source_evidence():
    selection, months = published_example(status="INCLUDED", outcomes=["PASS", "PASS", "PASS"])
    data = build_eligibility_dataset(selection, months)
    reason = data["tables"][3]["rows"][0]
    assert reason["row_id"] == "m0:not_applicable"
    assert reason["cells"]["month"]["canonical_value"] == "2026-07"
    assert reason["cells"]["reason_code"]["availability"] == "NOT_APPLICABLE"
    assert reason["cells"]["reason_code"]["reason_codes"] == ["NO_APPLICABLE_REASONS"]
    assert data["tables"][0]["rows"][0]["cells"]["excluded_count"]["canonical_value"] == "0"


@pytest.mark.parametrize("mutation", ["missing", "extra", "status", "wrong_month", "universe"])
def test_published_month_population_join_refuses_inconsistent_canonical_source(mutation):
    from app.composite_reporting.eligibility_admission import _require_published_population

    selection, months = published_example()
    member, universe = months[0]["membership"], months[0]["universe"]
    proposal = months[0]["receipt"]["approval"]["proposal"]
    _require_published_population(selection.months[0], member, universe, proposal)
    if mutation == "missing":
        member["decisions"].pop()
    elif mutation == "extra":
        member["decisions"].append({**member["decisions"][-1], "portfolio_id": "extra"})
    elif mutation == "status":
        member["decisions"][-1]["status"] = "INCLUDED"
    elif mutation == "wrong_month":
        member["decisions"][-1]["effective_from"] = "2026-08-01"
    else:
        universe["expected_portfolio_ids"] = ["extra"]
    with pytest.raises(ValueError, match="PUBLISHED_POPULATION_CONFLICT"):
        _require_published_population(selection.months[0], member, universe, proposal)


def test_nested_locator_hash_changes_are_bound_even_when_universe_legacy_hash_is_unchanged():
    from app.composite_reporting.eligibility_admission import source_hash

    selection, months = published_example()
    universe = months[0]["universe"]
    before = source_hash(universe, recursive=True)
    universe["source_products"][0]["content_hash"] = "sha256:" + "c" * 64
    assert source_hash(universe, recursive=True) == before
    with pytest.raises(ValueError, match="CAPTURE_DIGEST_CONFLICT"):
        build_eligibility_dataset(selection, months)


def test_interval_and_publication_join_guards_accept_valid_and_refuse_conflicts():
    from app.composite_reporting.eligibility_admission import (
        _require_history_intervals,
        _require_published_joins,
    )

    selection, months = published_example()
    month = months[0]
    _require_history_intervals(month["membership"])
    _require_published_joins(selection, selection.months[0], month)
    bad = deepcopy(month["membership"])
    bad["decisions"].append(deepcopy(bad["decisions"][-1]))
    with pytest.raises(ValueError, match="HISTORY_INTERVAL_CONFLICT"):
        _require_history_intervals(bad)
    month["publication"]["sequence"] += 1
    with pytest.raises(ValueError, match="PUBLICATION_JOIN_CONFLICT"):
        _require_published_joins(selection, selection.months[0], month)


def test_publication_cut_binds_retained_universe_not_observation_cut():
    from app.composite_reporting.eligibility_admission import _require_published_joins

    selection, months = published_example(universe_cut="retained-universe-cut")
    month = months[0]
    _require_published_joins(selection, selection.months[0], month)
    month["universe"]["source_cut_id"] = selection.months[0].source_cut_id
    with pytest.raises(ValueError, match="PUBLICATION_CUT_CONFLICT"):
        _require_published_joins(selection, selection.months[0], month)
    month["universe"]["source_cut_id"] = "retained-universe-cut"
    month["publication"]["source_cut_id"] = selection.months[0].source_cut_id
    with pytest.raises(ValueError, match="PUBLICATION_JOIN_CONFLICT"):
        _require_published_joins(selection, selection.months[0], month)
