"""The overview panel says what it shows and what it leaves out (#245)."""

from app.reporting_render.holdings_presentation import build_holdings_presentation
from app.reporting_render.package_builder import (
    PRESENTED_HOLDING_LIMIT,
    _ranked_holdings,
    _top_holdings,
)


def _snapshot(rows, *, notes=None, grouped=True, status="ready"):
    holdings = {"supportability": {"status": status, "notes": notes or []}}
    if grouped:
        holdings["holdingsByAssetClass"] = {"Equity": rows}
    return {"holdings": holdings}


def _holding(name, value, weight):
    return {
        "security_name": name,
        "market_value_reporting_currency": value,
        "weight": weight,
    }


def _built(snapshot):
    return build_holdings_presentation(
        snapshot,
        ranked=_ranked_holdings(snapshot),
        limit=PRESENTED_HOLDING_LIMIT,
    )


def test_a_concentrated_and_a_diversified_portfolio_are_told_apart():
    """The defect. Both draw five rows under "Portfolio scope"; in one those
    five are nearly the whole portfolio and in the other barely a third, and
    the page said the same thing either way."""

    concentrated = _built(_snapshot([_holding(f"H{i}", 1000 - i, 19.0) for i in range(5)]))
    diversified = _built(_snapshot([_holding(f"H{i}", 1000 - i, 6.0) for i in range(20)]))

    assert concentrated["presented_count"] == diversified["presented_count"] == 5
    assert concentrated["available_count"] == 5
    assert diversified["available_count"] == 20
    assert concentrated["presented_weight_pct"] == "95.00"
    assert diversified["presented_weight_pct"] == "30.00"


def test_the_reconciliation_describes_the_rows_actually_drawn():
    """A reconciliation describing a different set than the page is worse than
    none, because it looks checked. The panel and the count come from one
    ordered list, so the presented weight is the weight of the drawn rows."""

    snapshot = _snapshot(
        [_holding("Big", 900, 60.0), _holding("Small", 100, 5.0)]
        + [_holding(f"H{i}", 50 - i, 1.0) for i in range(10)]
    )

    drawn = _top_holdings(snapshot)
    presentation = _built(snapshot)

    assert [row["security_name"] for row in drawn][:2] == ["Big", "Small"]
    assert presentation["presented_count"] == len(drawn)
    # 60 + 5 + 1 + 1 + 1 for the five largest.
    assert presentation["presented_weight_pct"] == "68.00"


def test_a_portfolio_holding_nothing_is_not_a_sourcing_failure():
    """Opposite facts that both produce an empty list: one is about the
    PORTFOLIO and is drawn, the other about the DATA and is said."""

    empty = _built(_snapshot([]))
    unavailable = _built(_snapshot([], grouped=False))

    assert empty["posture"] == "empty"
    assert unavailable["posture"] == "unavailable"
    assert empty["available_count"] == 0


def test_fewer_holdings_than_the_limit_draws_no_misleading_reconciliation():
    """Three of three is the whole portfolio. The counts are equal, so a
    consumer can tell there is nothing left out rather than being told
    "3 of 3" as though something were."""

    presentation = _built(_snapshot([_holding(f"H{i}", 100 - i, 33.0) for i in range(3)]))

    assert presentation["presented_count"] == presentation["available_count"] == 3


def test_holdings_with_no_stated_weight_still_count_toward_the_whole():
    """A holding with no weight is still a holding. Dropping it from
    `available_count` would understate the portfolio and make the presented
    share look larger than it is."""

    snapshot = _snapshot(
        [_holding("Weighted", 500, 40.0)] + [_holding(f"H{i}", 100 - i, None) for i in range(9)]
    )

    presentation = _built(snapshot)

    assert presentation["available_count"] == 10
    assert presentation["presented_weight_pct"] == "40.00"


def test_no_stated_weight_at_all_says_so_rather_than_claiming_zero():
    """ "These five cover 0% of the portfolio" is a false statement; "the
    covered share could not be established" is a true one. The count
    reconciliation still holds, so the two fail independently."""

    presentation = _built(_snapshot([_holding(f"H{i}", 100 - i, None) for i in range(8)]))

    assert presentation["presented_weight_pct"] is None
    assert presentation["presented_count"] == 5
    assert presentation["available_count"] == 8


def test_data_quality_notes_reach_the_document():
    """A partial holdings read rendered byte-identically to a clean one.
    Reconciliation status is a statement about a client's own positions."""

    presentation = _built(
        _snapshot(
            [_holding("H", 100, 10.0)],
            notes=[{"code": "holdings_not_reconciled", "severity": "warning", "message": "m"}],
        )
    )

    assert presentation["notes"][0]["code"] == "holdings_not_reconciled"


def test_a_report_that_did_not_order_holdings_promises_nothing():
    assert build_holdings_presentation({}, ranked=[], limit=5) == {}


def test_empty_unavailable_and_unreconciled_are_three_different_statements():
    """The invariant. A portfolio that holds nothing, a portfolio whose
    holdings could not be sourced, and a portfolio whose holdings arrived
    unreconciled are three different statements about a client's own
    positions - and the first two are not degradations of trust while the
    third is.

    None of the three may render as any other. Asserted together rather than
    pairwise, because the failure that matters is two of them collapsing.
    """

    empty = _built(_snapshot([]))
    unavailable = _built(_snapshot([], grouped=False))
    unreconciled = _built(
        _snapshot(
            [_holding("H", 100, 10.0)],
            status="partial",
            notes=[
                {
                    "code": "holdings_not_reconciled",
                    "severity": "warning",
                    "message": "Positions are not reconciled.",
                }
            ],
        )
    )
    trusted = _built(_snapshot([_holding("H", 100, 10.0)]))

    assert empty["posture"] == "empty"
    assert unavailable["posture"] == "unavailable"
    assert unreconciled["posture"] == "ready"

    # ...and unreconciled must not look like trusted-complete either, which
    # posture alone cannot express: both are `ready`.
    assert unreconciled["supportability_status"] == "partial"
    assert trusted["supportability_status"] == "ready"
    assert unreconciled["notes"] and not trusted["notes"]

    distinct = {
        (entry["posture"], entry.get("supportability_status"))
        for entry in (empty, unavailable, unreconciled, trusted)
    }
    assert len(distinct) == 4


def test_partial_evidence_is_stated_not_inferred_from_a_note_count():
    """Core owns whether a position set is reconciled. Report forwards that
    verdict verbatim and never re-derives it - a second opinion with no
    evidence behind it - and never leaves a consumer to infer it from whether
    the note list happens to be non-empty."""

    presentation = _built(_snapshot([_holding("H", 100, 10.0)], status="partial", notes=[]))

    assert presentation["supportability_status"] == "partial"
    assert presentation["notes"] == []
