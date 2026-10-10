"""Manage211e's supplied A/B/C decisions with controlled January5 financial facts.

This portable literal contract fixture is not the pending native financial join.
"""

from copy import deepcopy

from app.composite_reporting.admission import response_digest
from app.composite_reporting.eligibility_admission import source_hash
from app.composite_reporting.membership_history import CapturedMembership, membership_endpoint
from app.composite_reporting.models import CompositeMembershipPin, CompositeSourceContextSelection
from tests.unit.composite_reporting.fixtures import selection_for
from tests.unit.composite_reporting.inception_examples import inception_example


def decision(member, first="2026-01-01", last=None, excluded=False):
    return {
        "portfolio_id": member,
        "effective_from": first,
        "effective_to": last,
        "status": "EXCLUDED" if excluded else "INCLUDED",
        "reason_code": "SYNTHETIC_POLICY_EXCLUSION" if excluded else None,
        "discretionary": True,
        "approval_ref": "synthetic-decision-approval",
        "source_snapshot_id": "synthetic-observation",
    }


def supplied_history_example(corrected=False, include_si=False):
    _, source, definition = inception_example()
    original = {
        "product_name": "CompositeMembership",
        "product_version": "v1",
        "tenant_id": "tenant-a",
        "composite_id": "NEUTRAL_BALANCED",
        "definition_version": "d1",
        "membership_revision": "m1",
        "policy_version": "synthetic-policy",
        "source_cut_id": "synthetic-cut-original",
        "decided_at": "2026-10-11T00:00:00Z",
        "decided_by": "synthetic-operator",
        "correlation_id": "synthetic-membership-original",
        "supersedes_membership_revision": None,
        "affected_from": None,
        "affected_to": None,
        "decisions": [
            decision("A"),
            decision("B", last="2026-01-31"),
            decision("B", first="2026-02-01", excluded=True),
            decision("C", last="2026-01-15"),
        ],
    }
    original["content_hash"] = source_hash(original)
    correction = deepcopy(original) | {
        "membership_revision": "m2",
        "source_cut_id": "synthetic-cut-corrected",
        "correlation_id": "synthetic-membership-corrected",
        "supersedes_membership_revision": "m1",
        "affected_from": "2026-01-05",
        "affected_to": "2026-01-05",
        "decisions": [
            decision("A", last="2026-01-04"),
            decision("A", first="2026-01-05", last="2026-01-05", excluded=True),
            decision("A", first="2026-01-06"),
            *original["decisions"][1:],
        ],
    }
    correction["content_hash"] = source_hash(correction)
    raws = [original, correction] if corrected else [original]
    captures = []
    for raw in raws:
        pin = CompositeMembershipPin(
            membership_revision=raw["membership_revision"],
            content_hash=raw["content_hash"],
            response_digest=response_digest(raw),
        )
        captures.append(
            CapturedMembership(
                pin=pin,
                endpoint=membership_endpoint(definition.endpoint, pin),
                method="GET",
                source_response_digest=pin.response_digest,
                source_response=raw,
            )
        )
    window = source["selection_manifest"]["windows"][0]
    window.update(
        period_start="2026-01-05",
        period_end="2026-01-05",
        membership_content_hash=raws[-1]["content_hash"],
    )
    source["selection_manifest"]["windows"] = [window]
    period = source["periods"][0]
    members = ["B", "C"] if corrected else ["A", "B", "C"]
    period.update(
        period_start="2026-01-05",
        period_end="2026-01-05",
        return_value="0.020000000000" if corrected else "0.010000000000",
        cumulative_return="0.020000000000" if corrected else "0.010000000000",
        beginning_market_value="200" if corrected else "300",
        ending_market_value="204" if corrected else "303",
        member_count=len(members),
        excluded_member_count=1 if corrected else 0,
    )
    template = period["member_contributions"][0]
    period["member_contributions"] = [
        deepcopy(template)
        | {
            "portfolio_id": member,
            "period_start": "2026-01-05",
            "period_end": "2026-01-05",
            "return_value": period["return_value"],
            "beginning_market_value": "100",
            "beginning_asset_weight": "0.5" if corrected else "0.333333333333",
            "contribution": "0.01" if corrected else "0.003333333333",
        }
        for member in members
    ]
    source.update(
        period_start="2026-01-05",
        period_end="2026-01-05",
        cumulative_return=period["cumulative_return"],
        periods=[period],
    )
    selection = selection_for(source)
    context = CompositeSourceContextSelection(
        definition=definition.pin,
        since_inception=include_si,
        memberships=[capture.pin for capture in captures],
    )
    return selection, source, definition, context, captures
