"""ContributionRanking: both signs, reconciled, and honest about the residual (#209).

Report captured a full ranked contribution set and sent Render one field of it -
the single largest positive contributor. A winners-only ranking is not a weaker
explanation of a period, it is a misleading one: it reads as the explanation
while omitting half the cause.
"""

from __future__ import annotations

from typing import Any

from app.reporting_render.contribution_ranking import (
    PRESENTED_CONTRIBUTOR_LIMIT,
    build_contribution_ranking,
)


def _snapshot(**overrides: Any) -> dict[str, Any]:
    contribution: dict[str, Any] = {
        "status": "present",
        "period": "YTD",
        "total_portfolio_return_pct": 7.93,
        "total_contribution_pct": 7.93,
        "methodology": {
            "basis": "NET",
            "weighting_scheme": "average_weight",
            "residual_allocation_applied": True,
            "residual_allocation_basis": "average_weight",
        },
        "top_position_contributors": [
            {
                "security_id": "SEC_A",
                "total_contribution_pct": 1.20,
                "average_weight_pct": 4.10,
                "total_return_pct": 29.30,
            },
            {
                "security_id": "SEC_B",
                "total_contribution_pct": -0.85,
                "average_weight_pct": 2.30,
                "total_return_pct": -31.10,
            },
            {
                "security_id": "SEC_C",
                "total_contribution_pct": 0.40,
                "average_weight_pct": 1.10,
                "total_return_pct": 4.00,
            },
        ],
    }
    contribution.update(overrides.pop("contribution", {}))
    snapshot: dict[str, Any] = {
        "performance": {"contribution": contribution},
        "holdings": {
            "holdingsByAssetClass": {
                "EQUITY": [
                    {"security_id": "SEC_A", "security_name": "Alphabet Inc Class A"},
                    {"security_id": "SEC_B", "security_name": "Vodafone Group PLC"},
                ]
            }
        },
    }
    snapshot.update(overrides)
    return snapshot


def test_losers_are_ranked_beside_winners() -> None:
    """The defect this block exists to remove: a ranking that shows only the
    largest positive contributor reads as an explanation of the period while
    omitting half the cause."""

    ranking = build_contribution_ranking(_snapshot())

    names = [row["name"] for row in ranking["contributors"]]
    # SEC_C has no holdings row, so it is named by the identifier that IS
    # known rather than by a placeholder - see the naming test below.
    assert names == ["Alphabet Inc Class A", "Vodafone Group PLC", "SEC_C"]
    assert ranking["contributors"][1]["contribution_pct"] == "-0.85"


def test_contributors_are_ordered_by_the_size_of_the_effect() -> None:
    """A -0.85 explains more of a period than a +0.40, so magnitude orders the
    ranking and the sign stays in the value."""

    ranking = build_contribution_ranking(_snapshot())

    assert [row["contribution_pct"] for row in ranking["contributors"]] == [
        "1.20",
        "-0.85",
        "0.40",
    ]


def test_names_are_joined_so_render_never_joins_anything() -> None:
    """Contribution rows carry identifiers; the readable name lives in
    holdings. That join is Report's composition work."""

    ranking = build_contribution_ranking(_snapshot())

    assert ranking["contributors"][0]["name"] == "Alphabet Inc Class A"
    # A contributor with no holdings row still ranks, named by what is known.
    assert ranking["contributors"][2]["name"] == "SEC_C"


def test_the_presented_set_reconciles_to_the_whole() -> None:
    """A top-N of a larger set explains only part of the period; without the
    reconciliation the page implies the list is the whole story."""

    contributors = [
        {"security_id": f"SEC_{index}", "total_contribution_pct": 1.0}
        for index in range(PRESENTED_CONTRIBUTOR_LIMIT + 5)
    ]
    ranking = build_contribution_ranking(
        _snapshot(contribution={"top_position_contributors": contributors})
    )

    assert ranking["presented_count"] == PRESENTED_CONTRIBUTOR_LIMIT
    assert ranking["available_count"] == PRESENTED_CONTRIBUTOR_LIMIT + 5
    assert ranking["presented_contribution_pct"] == "10.00"


def test_the_unexplained_residual_is_computed_not_left_to_a_reader() -> None:
    """`residual = total_portfolio_return - sum_of_contributions` is arithmetic
    on financial data, so Report states it rather than inviting a subtraction."""

    ranking = build_contribution_ranking(
        _snapshot(contribution={"total_portfolio_return_pct": 7.93, "total_contribution_pct": 7.50})
    )

    assert ranking["total_portfolio_return_pct"] == "7.93"
    assert ranking["explained_contribution_pct"] == "7.50"
    assert ranking["unexplained_residual_pct"] == "0.43"


def test_residual_allocation_posture_travels_with_the_numbers() -> None:
    """ "This ranking sums to the portfolio return" and "this ranking falls
    short" are different claims, and only the flag distinguishes them."""

    ranking = build_contribution_ranking(_snapshot())

    assert ranking["methodology"]["residual_allocation_applied"] is True
    assert ranking["methodology"]["residual_allocation_basis"] == "average_weight"
    assert ranking["methodology"]["basis"] == "NET"


def test_absent_methodology_is_published_absent_never_guessed() -> None:
    """NET versus GROSS changes what every number means and cannot be inferred
    from a value, so an absent basis is stated as absent - never defaulted."""

    ranking = build_contribution_ranking(
        _snapshot(contribution={"methodology": {"weighting_scheme": "average_weight"}})
    )

    assert ranking["methodology"]["basis"] is None
    assert ranking["methodology"]["residual_allocation_applied"] is None
    # The ranking is still drawn: the values are real and correctly ordered
    # whatever the basis, and refusing would remove the information as well as
    # the signal that something is missing.
    assert ranking["posture"] == "ready"
    assert ranking["contributors"]


def test_computed_with_nothing_to_rank_is_empty_not_unavailable() -> None:
    """A period with no movement is a fact about the PORTFOLIO and is drawn;
    a source that did not compute is a fact about the DATA and is said. An
    empty list cannot express that difference, which is why posture is
    authoritative."""

    empty = build_contribution_ranking(_snapshot(contribution={"top_position_contributors": []}))
    unavailable = build_contribution_ranking(
        _snapshot(contribution={"status": "unavailable", "reason_code": "contribution_not_sourced"})
    )

    assert empty["posture"] == "empty"
    assert empty["methodology"]["basis"] == "NET"
    assert unavailable["posture"] == "unavailable"
    assert "contributors" not in unavailable


def test_a_contributor_without_a_computed_value_is_not_drawn_as_zero() -> None:
    """ "No data" and "no movement" are different statements, and neither is a
    zero contribution."""

    ranking = build_contribution_ranking(
        _snapshot(
            contribution={
                "top_position_contributors": [
                    {"security_id": "SEC_A", "total_contribution_pct": 1.20},
                    {"security_id": "SEC_D", "total_contribution_pct": None},
                ]
            }
        )
    )

    assert [row["name"] for row in ranking["contributors"]] == ["Alphabet Inc Class A"]
    assert ranking["available_count"] == 1
