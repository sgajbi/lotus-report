"""Authored transport examples are not producer receipts or bank approvals."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from app.composite_reporting.admission import response_digest
from app.composite_reporting.eligibility_contract import CompositeEligibilityReportData
from app.composite_reporting.eligibility_tables import build_eligibility_dataset
from app.composite_reporting.models import EligibilitySelection


def seal(value, recursive=False):
    def strip(item):
        if isinstance(item, dict):
            return {key: strip(child) for key, child in item.items() if key != "content_hash"}
        if isinstance(item, list):
            return [strip(child) for child in item]
        return item

    body = (
        strip(value)
        if recursive
        else {key: item for key, item in value.items() if key != "content_hash"}
    )
    value["content_hash"] = (
        "sha256:"
        + sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    return value


def evaluated_example(month="2026-07"):
    """Literal two-member source decisions: one excluded, one missing observation."""
    scope = {
        "tenant_id": "test-tenant",
        "composite_id": "test-composite",
        "definition_version": "d1",
    }
    policy = seal(
        {
            "product_name": "CompositeMonthlyEligibilityPolicy",
            "product_version": "v1",
            "profile_kind": "SYNTHETIC_MONTHLY_ABS_NET_CASH_READINESS",
            "official_activation": "UNAVAILABLE",
            "month": month,
            "scope": {**scope, "strategy_code": "BALANCED", "run_id": None},
            "layers": [{"level": "PLATFORM", "policy_id": "test-policy", "revision": "r1"}],
            "flow_threshold": "0.10",
            "cash_threshold": "0.05",
            "flow_measure": "ABS_NET",
            "flow_breach_operator": "GREATER_THAN_OR_EQUAL",
            "cash_breach_operator": "GREATER_THAN",
            "reentry": "REEVALUATE_ALL_NEXT_MONTH_RULES",
            "membership_frequency": "CALENDAR_MONTH",
            "flow_denominator": "PRIOR_MONTH_END_NET_ASSETS",
            "cash_denominator": "MONTH_END_NET_ASSETS",
            "cash_numerator": "SETTLED_UNENCUMBERED_CASH_ONLY",
            "ratio_unit": "DECIMAL_FRACTION",
            "flow_date_basis": "SOURCE_BUSINESS_DATE_UTC",
            "holiday_treatment": "NO_DATE_SHIFT",
            "currency_treatment": "SOURCE_NORMALIZED_SINGLE_CURRENCY",
            "observation_window": "WHOLE_TARGET_MONTH",
            "evaluation_timing": "AFTER_MONTH_END",
            "source_cut_timing": "AFTER_MONTH_END",
            "missing_data": "REQUIRED_UNKNOWN",
        }
    )
    policy_proposal = seal(
        {
            "product_name": "CompositeMonthlyPolicyProposal",
            "product_version": "v1",
            "proposal_revision": "policy-r1",
            "eligibility_policy_version": "p1",
            "policy": policy,
            "attachments": [],
            "proposed_by": "test-maker",
            "proposed_at": "2026-06-01T00:00:00.000000Z",
        }
    )
    policy_approval = seal(
        {
            "product_name": "CompositeMonthlyPolicyApproval",
            "product_version": "v1",
            "evidence_kind": "SYNTHETIC_UNSIGNED",
            "official_activation": "UNAVAILABLE",
            "proposal": policy_proposal,
            "approved_by": "test-checker",
            "approved_at": "2026-06-02T00:00:00.000000Z",
        }
    )
    observations = {
        "product_name": "CompositeMonthlyEligibilityObservations",
        "product_version": "v1",
        "evidence_class": "SYNTHETIC_UNQUALIFIED",
        **scope,
        "month": month,
        "source_cut_id": "cut-1",
        "source_revision": "obs-r1",
        "reporting_currency": "USD",
        "source_generated_at": "2026-10-01T00:00:00.000000Z",
        "expected_portfolio_ids": ["excluded", "missing"],
        "portfolios": [
            {
                "portfolio_id": "excluded",
                "currency": "USD",
                "flows": [],
                "prior_month_end_assets": "1000.00",
                "prior_assets_as_of": "2026-06-30",
                "month_end_assets": "1000.00",
                "settled_unencumbered_cash": "51.00",
                "cash_as_of": month + "-31",
                "discretionary": True,
                "funded": False,
                "invested": None,
                "readiness_as_of": month + "-31",
                "flow_coverage_from": month + "-01",
                "flow_coverage_to": month + "-31",
            }
        ],
    }
    parent_hash = "sha256:" + "1" * 64
    observation_hash = seal(deepcopy(observations))["content_hash"]
    universe = seal(
        {
            "product_name": "CompositeUniverseAttestation",
            "product_version": "v1",
            **scope,
            "membership_revision": "parent-r1",
            "membership_content_hash": parent_hash,
            "attestation_version": "input-u1",
            "coverage_from": month + "-01",
            "coverage_to": month + "-31",
            "policy_version": "p1",
            "source_cut_id": "cut-1",
            "source_products": [
                {
                    "owner_service": "lotus-manage",
                    "product_name": observations["product_name"],
                    "contract_version": "v1",
                    "authority_scope": "POLICY_INPUT",
                    "source_cut_id": "cut-1",
                    "source_watermark": "obs-r1",
                    "content_hash": observation_hash,
                }
            ],
            "posture": "INCOMPLETE",
            "expected_portfolio_ids": ["excluded", "missing"],
            "expected_portfolio_count": 2,
            "observed_portfolio_count": 1,
            "missing_portfolio_ids": ["missing"],
            "unexpected_portfolio_ids": [],
            "coverage_gap_portfolio_ids": [],
            "reason_code": "UNVERIFIED",
            "attested_at": "2026-10-01T00:00:00.000000Z",
            "attested_by": "test-source",
            "correlation_id": "test-correlation",
        },
        recursive=True,
    )
    assessments = [
        {
            "rule": rule,
            "outcome": "FAIL",
            "failure_reasons": [reason],
            "unknown_reasons": ["READINESS_INVESTED_UNKNOWN"] if rule == "READINESS" else [],
            "numerator": "100.00" if rule == "SIGNIFICANT_FLOW" else None,
            "denominator": "1000.00" if rule == "SIGNIFICANT_FLOW" else None,
            "ratio": "0.1" if rule == "SIGNIFICANT_FLOW" else None,
            "gross_inflow": "100.00" if rule == "SIGNIFICANT_FLOW" else None,
            "gross_outflow": "0.00" if rule == "SIGNIFICANT_FLOW" else None,
            "admitted_flow_count": 1 if rule == "SIGNIFICANT_FLOW" else None,
        }
        for rule, reason in (
            ("SIGNIFICANT_FLOW", "SIGNIFICANT_FLOW_THRESHOLD_BREACH"),
            ("CASH", "CASH_THRESHOLD_BREACH"),
            ("READINESS", "READINESS_FUNDED_FAILED"),
        )
    ]
    missing = [
        {
            **item,
            "outcome": "UNKNOWN",
            "failure_reasons": [],
            "unknown_reasons": ["MISSING_EXPECTED_PORTFOLIO_OBSERVATIONS"],
            **{
                field: None
                for field in (
                    "numerator",
                    "denominator",
                    "ratio",
                    "gross_inflow",
                    "gross_outflow",
                    "admitted_flow_count",
                )
            },
        }
        for item in assessments
    ]
    evaluation = seal(
        {
            "product_name": "CompositeMonthlyEligibilityEvaluation",
            "product_version": "v1",
            "evidence_class": "SYNTHETIC_UNQUALIFIED",
            "official_activation": "UNAVAILABLE",
            "population_verification": "UNVERIFIED",
            **scope,
            "month": month,
            "source_cut_id": "cut-1",
            "source_revision": "obs-r1",
            "evaluated_at": "2026-10-01T00:00:00.000000Z",
            "input_content_hash": observation_hash,
            "universe_content_hash": universe["content_hash"],
            "resolved_policy": policy,
            "declared_universe_coverage": "INCOMPLETE",
            "expected_count": 2,
            "observed_count": 1,
            "included_count": 0,
            "excluded_count": 1,
            "pending_review_count": 1,
            "portfolios": [
                {
                    "portfolio_id": "excluded",
                    "observations_present": True,
                    "status": "EXCLUDED",
                    "assessments": assessments,
                },
                {
                    "portfolio_id": "missing",
                    "observations_present": False,
                    "status": "PENDING_REVIEW",
                    "assessments": missing,
                },
            ],
        }
    )
    proposal = seal(
        {
            "product_name": "CompositeMonthlyEvaluationProposal",
            "product_version": "v1",
            "evaluation_revision": "eval-" + month,
            "target_membership_revision": "target-" + month,
            "parent_membership_revision": "parent-r1",
            "parent_membership_content_hash": parent_hash,
            "policy_approval": policy_approval,
            "universe": universe,
            "observations": observations,
            "evaluation": evaluation,
            "proposed_by": "test-maker",
            "proposed_at": evaluation["evaluated_at"],
            "correlation_id": "test-correlation",
        }
    )
    pin = {
        "evidence_kind": "EVALUATED_ONLY",
        "month": month,
        "evaluation_revision": proposal["evaluation_revision"],
        "proposal_content_hash": proposal["content_hash"],
        "proposal_response_digest": response_digest(proposal),
        "parent_membership_revision": "parent-r1",
        "parent_membership_content_hash": parent_hash,
        "source_cut_id": "cut-1",
    }
    selection = EligibilitySelection.model_validate(
        {
            **scope,
            "reporting_currency": "USD",
            "period_start": month + "-01",
            "period_end": month + "-31",
            "months": [pin],
        }
    )
    return selection, [
        {
            "evidence_kind": "EVALUATED_ONLY",
            "proposal": proposal,
            "response_digests": {"proposal": response_digest(proposal)},
        }
    ]


def test_complete_assessments_missing_observations_and_unique_counts():
    selection, months = evaluated_example()
    original = deepcopy(months)
    data = build_eligibility_dataset(selection, months)
    tables = {table["table_id"]: table for table in data["tables"]}
    assert list(tables) == [
        "Summary",
        "Members",
        "EligibilityAssessments",
        "EligibilityReasons",
        "MembershipHistory",
        "Methods",
        "Lineage",
        "Disclosures",
    ]
    assert len(tables["Members"]["rows"]) == 2
    assert len(tables["EligibilityAssessments"]["rows"]) == 6
    assert len(tables["EligibilityReasons"]["rows"]) == 7
    assert tables["Summary"]["rows"][0]["cells"]["excluded_count"]["canonical_value"] == "1"
    assert (
        tables["Members"]["rows"][1]["cells"]["observations_present"]["canonical_value"] == "false"
    )
    assert (
        tables["MembershipHistory"]["rows"][0]["cells"]["status"]["availability"] == "UNAVAILABLE"
    )
    assert months == original
    assert data["publication_state"] == "NOT_ATTESTED"


@pytest.mark.parametrize(
    "mutation",
    [
        "member",
        "assessment",
        "reason",
        "column",
        "table",
        "precision",
        "unit",
        "pointer",
        "false_null",
        "disclosure",
        "qualification",
    ],
)
def test_complete_projection_refuses_corruption(mutation):
    selection, months = evaluated_example()
    data = build_eligibility_dataset(selection, months)
    tables = {table["table_id"]: table for table in data["tables"]}
    if mutation in {"member", "assessment", "reason"}:
        tables[
            {
                "member": "Members",
                "assessment": "EligibilityAssessments",
                "reason": "EligibilityReasons",
            }[mutation]
        ]["rows"].pop()
    elif mutation == "column":
        tables["Members"]["columns"].pop()
    elif mutation == "table":
        data["tables"].pop()
    elif mutation in {"precision", "unit"}:
        column = tables["EligibilityAssessments"]["columns"][6]
        column["display_decimal_places" if mutation == "precision" else "unit"] = (
            2 if mutation == "precision" else "TEXT"
        )
    elif mutation in {"pointer", "false_null"}:
        cell = tables["Members"]["rows"][1]["cells"]["status"]
        cell["source_pointer" if mutation == "pointer" else "canonical_value"] = (
            "/report_facts/unavailable" if mutation == "pointer" else None
        )
    elif mutation == "disclosure":
        data["report_facts"]["disclosures"].pop()
    else:
        data["publication_state"] = "ATTESTED"
    with pytest.raises(ValueError):
        CompositeEligibilityReportData.model_validate(data)


@pytest.mark.parametrize(
    "mutation", ["tenant", "month", "population", "duplicate", "rule", "hash", "kind", "approval"]
)
def test_source_refusals(mutation):
    selection, months = evaluated_example()
    proposal = months[0]["proposal"]
    if mutation == "tenant":
        proposal["evaluation"]["tenant_id"] = "foreign"
    elif mutation == "month":
        proposal["evaluation"]["month"] = "2026-08"
    elif mutation == "population":
        proposal["evaluation"]["portfolios"].pop()
    elif mutation == "duplicate":
        proposal["evaluation"]["portfolios"][1] = deepcopy(proposal["evaluation"]["portfolios"][0])
    elif mutation == "rule":
        proposal["evaluation"]["portfolios"][0]["assessments"].reverse()
    elif mutation == "hash":
        proposal["universe"]["source_products"][0]["content_hash"] = "sha256:" + "f" * 64
    elif mutation == "kind":
        months[0]["evidence_kind"] = "PUBLISHED"
    else:
        months[0]["receipt"] = {"approval": "invented"}
    with pytest.raises(ValueError):
        build_eligibility_dataset(selection, months)


def test_source_null_slots_distinguish_rule_not_applicable_from_missing_evidence():
    selection, months = evaluated_example()
    data = build_eligibility_dataset(selection, months)
    assessments = data["tables"][2]["rows"]
    # READINESS does not use ratios; missing SIGNIFICANT_FLOW observations do.
    assert assessments[2]["cells"]["ratio"]["availability"] == "NOT_APPLICABLE"
    assert assessments[2]["cells"]["ratio"]["reason_codes"] == ["RULE_FIELD_NOT_APPLICABLE"]
    assert assessments[3]["cells"]["ratio"]["availability"] == "UNAVAILABLE"


def test_source_scope_and_policy_locator_guards_reject_self_consistent_transport_shapes():
    from app.composite_reporting.eligibility_admission import _require_policy, _require_scope

    selection, months = evaluated_example()
    proposal = months[0]["proposal"]
    _require_scope(selection, proposal["evaluation"])
    _require_policy(selection, "2026-07", proposal)
    with pytest.raises(ValueError, match="SOURCE_SCOPE_CONFLICT"):
        _require_scope(selection, {**proposal["evaluation"], "tenant_id": "foreign"})
    proposal["universe"]["source_products"][0]["content_hash"] = "sha256:" + "e" * 64
    with pytest.raises(ValueError, match="OBSERVATION_LOCATOR_CONFLICT"):
        _require_policy(selection, "2026-07", proposal)


def test_dataset_budget_counts_retained_raw_source_plus_complete_projection():
    selection, months = evaluated_example()
    proposal = months[0]["proposal"]
    proposal["unit_test_padding"] = "x" * 8_360_000
    seal(proposal)
    digest = response_digest(proposal)
    assert len(json.dumps(proposal, ensure_ascii=False, separators=(",", ":")).encode()) < 8_388_608
    pin = selection.months[0].model_copy(
        update={
            "proposal_content_hash": proposal["content_hash"],
            "proposal_response_digest": digest,
        }
    )
    selection = selection.model_copy(update={"months": [pin]})
    months[0]["response_digests"]["proposal"] = digest
    with pytest.raises(ValueError, match="DATASET_CAPACITY_EXCEEDED"):
        build_eligibility_dataset(selection, months)
